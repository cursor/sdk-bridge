"""Bridge process manager: locate, spawn, handshake, and shut down.

Implements the lifecycle in ``docs/protocol.md``: spawn the executable with
``CURSOR_API_KEY`` in its environment, scan **stderr** for the
``cursor-sdk-bridge ready `` discovery line, validate it, and read the bearer
token from ``authTokenFile``. Registers an ``atexit`` hook so a crashed
caller cannot leak bridge processes.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any

from ._errors import BridgeProcessError

_READY_LINE_PREFIX = "cursor-sdk-bridge ready "
_STARTUP_TIMEOUT = 30.0
_SHUTDOWN_TIMEOUT = 5.0


@dataclass
class BridgeEndpoint:
    """Where a running bridge listens and how to authenticate to it."""

    url: str
    auth_token: str
    server_version: str | None = None
    pid: int | None = None


def _default_binary() -> str:
    binary = os.environ.get("CURSOR_SDK_BRIDGE_BIN")
    if binary:
        return binary
    executable = "cursor-sdk-bridge.exe" if sys.platform == "win32" else "cursor-sdk-bridge"
    return os.path.join(".", "cursor-sdk-bridge", "bin", executable)


class BridgeManager:
    """Owns one ``cursor-sdk-bridge`` child process.

    A published SDK would also download and cache the platform archive here
    (``cursor-sdk-bridge-standalone-<os>-<arch>.tar.gz`` from this repo's
    GitHub releases); this example expects the binary to exist already
    (``fetch-bridge.sh``) or to be named via ``CURSOR_SDK_BRIDGE_BIN`` / the
    ``binary`` argument.
    """

    def __init__(
        self,
        binary: str | None = None,
        workspace: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._binary = binary or _default_binary()
        self._workspace = os.path.abspath(workspace or os.getcwd())
        self._api_key = api_key
        self._process: subprocess.Popen[str] | None = None

    def start(self) -> BridgeEndpoint:
        """Spawn the bridge and block until the ready-line handshake completes."""
        env = os.environ | {"CURSOR_SDK_CLIENT_LANGUAGE": "python"}
        if self._api_key:
            env["CURSOR_API_KEY"] = self._api_key
        try:
            self._process = subprocess.Popen(
                [self._binary, "--workspace", self._workspace],
                env=env,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as err:
            raise BridgeProcessError(
                f"could not launch bridge {self._binary!r}: {err} "
                "(run fetch-bridge.sh or set CURSOR_SDK_BRIDGE_BIN)"
            ) from None
        atexit.register(self.stop)

        discovery = self._await_ready_line(self._process)
        if (
            discovery.get("schemaVersion") != 1
            or discovery.get("transport") != "tcp"
            or discovery.get("protocol") != "connect"
        ):
            self.stop()
            raise BridgeProcessError(
                "unsupported bridge discovery: "
                f"schema={discovery.get('schemaVersion')!r} "
                f"transport={discovery.get('transport')!r} "
                f"protocol={discovery.get('protocol')!r}"
            )

        token_file = discovery.get("authTokenFile", "")
        try:
            with open(token_file, encoding="utf-8") as handle:
                token = handle.read().strip()
        except OSError as err:
            self.stop()
            raise BridgeProcessError(f"could not read auth token file: {err}") from None
        if not token:
            self.stop()
            raise BridgeProcessError(f"auth token file {token_file!r} is empty")

        return BridgeEndpoint(
            url=discovery["url"],
            auth_token=token,
            server_version=discovery.get("serverVersion"),
            pid=discovery.get("pid"),
        )

    def _await_ready_line(self, process: subprocess.Popen[str]) -> dict[str, Any]:
        """Scan stderr for the discovery line; keep draining forever after.

        A full stderr pipe blocks the bridge, so the scanner thread never
        stops reading. The discovery line is never logged verbatim — older
        bridges inline the auth token.
        """
        found: queue.Queue[dict[str, Any] | Exception] = queue.Queue(maxsize=1)

        def scan() -> None:
            assert process.stderr is not None
            diagnostics: list[str] = []
            for line in process.stderr:
                line = line.rstrip("\n")
                if not line.startswith(_READY_LINE_PREFIX):
                    diagnostics.append(line)  # Ordinary bridge diagnostics.
                    continue
                try:
                    found.put(json.loads(line[len(_READY_LINE_PREFIX) :]))
                except ValueError as err:
                    found.put(BridgeProcessError(f"invalid discovery JSON: {err}"))
                for _ in process.stderr:
                    pass
                return
            found.put(
                BridgeProcessError(
                    "bridge exited before emitting discovery: " + "\n".join(diagnostics)
                )
            )

        threading.Thread(target=scan, daemon=True).start()
        try:
            result = found.get(timeout=_STARTUP_TIMEOUT)
        except queue.Empty:
            self.stop()
            raise BridgeProcessError(
                f"timed out after {_STARTUP_TIMEOUT:.0f}s waiting for the bridge ready line"
            ) from None
        if isinstance(result, Exception):
            self.stop()
            raise result
        return result

    def stop(self) -> None:
        """Stop the child: SIGTERM (the bridge handles it), wait, then kill.

        Idempotent, and a no-op once the process has exited — the client
        prefers the graceful ``Shutdown`` RPC and calls this afterwards.
        """
        process, self._process = self._process, None
        if process is None:
            return
        atexit.unregister(self.stop)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait()
