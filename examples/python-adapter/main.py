#!/usr/bin/env python3
"""Minimal Cursor SDK bridge adapter in Python: spawns the bridge, performs
the ready-line handshake, authenticates with the bearer token, runs one local
agent turn, and streams the response to stdout.

See ../../docs/protocol.md for the lifecycle this implements. The RPC layer
is hand-rolled Connect-over-HTTP/1.1 on the standard library (the bridge does
not speak classic gRPC), so the only dependency is the protobuf runtime.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "gen"))

from google.protobuf import json_format
from google.protobuf.message import Message

from sdk.v1 import (
    sdk_agent_service_pb2,
    sdk_bridge_control_service_pb2,
    sdk_cursor_service_pb2,
    sdk_errors_pb2,
    sdk_messages_pb2,
)

READY_LINE_PREFIX = "cursor-sdk-bridge ready "
STARTUP_TIMEOUT = 30.0
SHUTDOWN_TIMEOUT = 5.0


class BridgeError(Exception):
    pass


@dataclass
class ConnectError(Exception):
    """A failed RPC: the Connect code plus the sdk.v1.SdkErrorDetails detail
    when the bridge attached one (see ../../docs/errors.md)."""

    code: str
    message: str
    details: sdk_errors_pb2.SdkErrorDetails | None = None

    def __str__(self) -> str:
        text = f"{self.code}: {self.message}"
        if self.details is not None:
            sdk_code = sdk_errors_pb2.SdkErrorCode.Name(self.details.sdk_error_code)
            text += (
                f" (sdk_error_code={sdk_code.removeprefix('SDK_ERROR_CODE_')}"
                f" request_id={self.details.request_id})"
            )
        return text


def parse_connect_error(payload: bytes) -> ConnectError:
    """Parse a Connect error JSON body: {"code", "message", "details": [...]}.
    Details are google.protobuf.Any values with a base64 "value"."""
    try:
        body = json.loads(payload)
    except ValueError:
        return ConnectError(code="unknown", message=payload.decode(errors="replace"))
    error = ConnectError(code=body.get("code", "unknown"), message=body.get("message", ""))
    for detail in body.get("details", []):
        if detail.get("type", "").endswith("sdk.v1.SdkErrorDetails"):
            raw = detail.get("value", "")
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            error.details = sdk_errors_pb2.SdkErrorDetails.FromString(decoded)
    return error


class BridgeClient:
    """Connect protocol client for one bridge endpoint. Every RPC is
    `POST {base_url}/sdk.v1.<Service>/<Method>` with a binary protobuf body
    and `Authorization: Bearer <token>` (see ../../docs/protocol.md)."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _post(self, path: str, content_type: str, body: bytes, timeout: float | None):
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "Content-Type": content_type,
                "Authorization": f"Bearer {self.token}",
                "Connect-Protocol-Version": "1",
            },
        )
        return urllib.request.urlopen(request, timeout=timeout)

    def unary(self, service: str, method: str, request: Message, response: Message) -> Message:
        try:
            with self._post(
                f"/sdk.v1.{service}/{method}",
                "application/proto",
                request.SerializeToString(),
                timeout=30.0,
            ) as reply:
                response.ParseFromString(reply.read())
                return response
        except urllib.error.HTTPError as err:
            # Connect unary errors: non-200 status, JSON body with the code.
            raise parse_connect_error(err.read()) from None

    def server_stream(
        self, service: str, method: str, request: Message, response_type: type[Message]
    ) -> Iterator[Message]:
        """Server-streaming Connect RPC (see ../../docs/streaming.md).

        Both directions use enveloped framing: each frame is 1 flags byte +
        4-byte big-endian payload length + payload. The request carries one
        framed message; response frames carry protobuf payloads until a frame
        with flags bit 0x02 — the JSON EndStreamResponse, which holds the
        error if the stream failed. The HTTP status is always 200.
        """
        payload = request.SerializeToString()
        body = struct.pack(">BI", 0, len(payload)) + payload
        # No read timeout: run streams idle during long tool calls, and the
        # bridge sends keepalives only every ~15s.
        with self._post(
            f"/sdk.v1.{service}/{method}", "application/connect+proto", body, timeout=None
        ) as reply:
            while True:
                header = _read_exact(reply, 5)
                flags, length = struct.unpack(">BI", header)
                payload = _read_exact(reply, length)
                if flags & 0x02:  # EndStreamResponse: JSON, maybe with an error.
                    end = json.loads(payload) if payload else {}
                    if "error" in end:
                        raise parse_connect_error(json.dumps(end["error"]).encode())
                    return
                message = response_type()
                message.ParseFromString(payload)
                yield message


def _read_exact(stream: Any, count: int) -> bytes:
    chunks = b""
    while len(chunks) < count:
        chunk = stream.read(count - len(chunks))
        if not chunk:
            raise BridgeError("run stream ended without an EndStreamResponse frame")
        chunks += chunk
    return chunks


def spawn_bridge(bridge_bin: str, workspace: str) -> tuple[subprocess.Popen, dict[str, Any]]:
    """Start the launcher and scan stderr for the ready line
    (see "Spawning and the ready line" in ../../docs/protocol.md)."""
    bridge_bin = (
        bridge_bin
        or os.environ.get("CURSOR_SDK_BRIDGE_BIN")
        or "./cursor-sdk-bridge/bin/cursor-sdk-bridge"
    )
    env = os.environ | {"CURSOR_SDK_CLIENT_LANGUAGE": "python"}
    process = subprocess.Popen(
        [bridge_bin, "--workspace", workspace],
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    )

    found: queue.Queue[dict[str, Any] | Exception] = queue.Queue(maxsize=1)

    def scan_stderr() -> None:
        assert process.stderr is not None
        diagnostics: list[str] = []
        for line in process.stderr:
            line = line.rstrip("\n")
            if not line.startswith(READY_LINE_PREFIX):
                diagnostics.append(line)  # Ordinary bridge diagnostics.
                continue
            try:
                found.put(json.loads(line[len(READY_LINE_PREFIX) :]))
            except ValueError as err:
                found.put(BridgeError(f"invalid discovery JSON: {err}"))
            # Keep draining stderr so the bridge never blocks on a full pipe.
            for _ in process.stderr:
                pass
            return
        found.put(
            BridgeError("bridge exited before emitting discovery: " + "\n".join(diagnostics))
        )

    threading.Thread(target=scan_stderr, daemon=True).start()

    try:
        result = found.get(timeout=STARTUP_TIMEOUT)
    except queue.Empty:
        process.kill()
        raise BridgeError(
            f"timed out after {STARTUP_TIMEOUT:.0f}s waiting for the bridge ready line"
        ) from None
    if isinstance(result, Exception):
        process.kill()
        raise result
    if (
        result.get("schemaVersion") != 1
        or result.get("transport") != "tcp"
        or result.get("protocol") != "connect"
    ):
        process.kill()
        raise BridgeError(
            "unsupported bridge discovery: "
            f"schema={result.get('schemaVersion')!r} transport={result.get('transport')!r} "
            f"protocol={result.get('protocol')!r}"
        )
    print(f"bridge ready: url={result['url']} serverVersion={result.get('serverVersion')}")
    return process, result


def print_stream_message(message: sdk_messages_pb2.RunStreamMessage) -> None:
    """Render one RunStreamMessage. Messages with no envelope case set are
    keepalives and must be ignored (see ../../docs/streaming.md)."""
    envelope = message.WhichOneof("envelope")
    if envelope == "sdk_message":
        print_sdk_message(message.sdk_message)
    elif envelope == "result":
        result = message.result
        status = sdk_messages_pb2.RunLifecycleStatus.Name(result.status)
        print(f"run finished: status={status.removeprefix('RUN_LIFECYCLE_STATUS_')}")
        if result.error_code:
            print(f"error code: {result.error_code}")
        if result.result.result:
            print(f"final result:\n{result.result.result}")
    elif envelope == "done":
        pass  # End-of-stream marker; the stream closes normally afterwards.
    elif envelope is None:
        pass  # Keepalive frame (empty envelope): ignore.
    else:
        pass  # Unknown envelope case from a newer bridge: ignore.


def print_sdk_message(message: sdk_messages_pb2.SdkMessage) -> None:
    payload = json_format.MessageToDict(message.message)
    if message.type == "system":
        if run_id := payload.get("run_id"):
            print(f"[system] run started: {run_id}")
    elif message.type == "assistant":
        # Assistant payloads are {message: {content: [{type: "text", ...}]}}.
        for block in payload.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text"):
                print(f"[assistant] {block['text']}")
    elif message.type == "tool_call":
        print(f"[tool_call {payload.get('status', '')}] {payload.get('name', '')}")
    elif message.type == "status":
        # Status payloads carry the lifecycle status and, on failure, the
        # human-readable error text.
        print(f"[status {payload.get('status', '')}] {payload.get('message', '')}".rstrip())
    else:
        print(f"[{message.type}]")


def run(bridge_bin: str, workspace: str, prompt: str, model: str) -> None:
    workspace = os.path.abspath(workspace)

    # --- 1. Spawn the bridge and wait for the ready line on stderr. ---
    process, discovery = spawn_bridge(bridge_bin, workspace)
    try:
        # --- 2. Read the bearer token from authTokenFile. ---
        with open(discovery["authTokenFile"], encoding="utf-8") as token_file:
            token = token_file.read().strip()
        if not token:
            raise BridgeError("auth token file is empty")

        client = BridgeClient(discovery["url"], token)

        # --- 3. Verify the connection. ---
        client.unary(
            "SdkBridgeControlService",
            "Ping",
            sdk_bridge_control_service_pb2.PingRequest(),
            sdk_bridge_control_service_pb2.PingResponse(),
        )
        version = client.unary(
            "SdkBridgeControlService",
            "GetVersion",
            sdk_bridge_control_service_pb2.GetVersionRequest(),
            sdk_bridge_control_service_pb2.GetVersionResponse(),
        )
        print(f"ping ok; bridge {version.bridge_version} protocol {version.protocol_version}")

        # --- 4. Create a local agent in the workspace. Local agents require
        # an explicit model; discover one via SdkCursorService when not given.
        if not model:
            # Catalog calls fail closed without an explicit api_key (no env
            # fallback), unlike agent operations.
            models = client.unary(
                "SdkCursorService",
                "ListModels",
                sdk_cursor_service_pb2.ListModelsRequest(
                    options=sdk_cursor_service_pb2.CursorRequestOptions(
                        api_key=os.environ["CURSOR_API_KEY"]
                    )
                ),
                sdk_cursor_service_pb2.ListModelsResponse(),
            )
            if not models.items:
                raise BridgeError("no models available to this account")
            model = models.items[0].id
        options = sdk_messages_pb2.AgentOptions(
            model=sdk_messages_pb2.ModelSelection(id=model),
            local=sdk_messages_pb2.LocalAgentOptions(cwd=[workspace]),
        )
        created = client.unary(
            "SdkAgentService",
            "CreateAgent",
            sdk_agent_service_pb2.CreateAgentRequest(options=options),
            sdk_agent_service_pb2.CreateAgentResponse(),
        )
        print(f"agent created: {created.agent_id} (model {created.model.id})")

        # --- 5. Send one message and stream the run. ---
        stream = client.server_stream(
            "SdkAgentService",
            "Send",
            sdk_agent_service_pb2.SendRequest(
                agent_id=created.agent_id,
                message=sdk_messages_pb2.UserMessage(text=prompt),
            ),
            sdk_messages_pb2.RunStreamMessage,
        )
        for message in stream:
            print_stream_message(message)

        # --- 6. Graceful shutdown: Shutdown RPC, then wait, then kill. ---
        try:
            client.unary(
                "SdkBridgeControlService",
                "Shutdown",
                sdk_bridge_control_service_pb2.ShutdownRequest(),
                sdk_bridge_control_service_pb2.ShutdownResponse(),
            )
        except (ConnectError, OSError):
            pass
        try:
            process.wait(timeout=SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
        print("bridge stopped")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--bridge",
        default="",
        help="path to the bridge launcher (default: $CURSOR_SDK_BRIDGE_BIN, "
        "then ./cursor-sdk-bridge/bin/cursor-sdk-bridge)",
    )
    parser.add_argument("--workspace", default=".", help="workspace directory for the local agent")
    parser.add_argument(
        "--prompt",
        default="Say hello and name one file in this workspace.",
        help="user message to send",
    )
    parser.add_argument(
        "--model",
        default="",
        help="model id (discovered via SdkCursorService.ListModels when empty)",
    )
    args = parser.parse_args()

    if not os.environ.get("CURSOR_API_KEY"):
        sys.exit("error: CURSOR_API_KEY must be set (create a key at https://cursor.com/dashboard)")
    try:
        run(args.bridge, args.workspace, args.prompt, args.model)
    except (BridgeError, ConnectError, OSError) as err:
        sys.exit(f"error: {err}")


if __name__ == "__main__":
    main()
