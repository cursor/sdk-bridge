#!/usr/bin/env python3
"""Run one agent turn through the cursor_adapter example SDK.

Demonstrates the public surface only — no proto or wire-protocol types:
spawn/attach is hidden inside Client, the turn streams through Run, and the
terminal result is a plain RunResult. See cursor_adapter/ for the layers
underneath (bridge manager, transport, errors) and the README for setup.
"""

from __future__ import annotations

import argparse
import os
import sys

from cursor_adapter import Client, CursorSdkError, RunEvent


def describe(event: RunEvent) -> str | None:
    """One printable line per interesting stream event."""
    if event.type == "system":
        return f"[system] run started: {event.payload.get('run_id', '?')}"
    if event.type == "assistant":
        blocks = event.payload.get("message", {}).get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return f"[assistant] {text}" if text else None
    if event.type == "tool_call":
        return f"[tool_call {event.payload.get('status', '')}] {event.payload.get('name', '')}"
    if event.type == "status":
        return f"[status {event.payload.get('status', '')}] {event.payload.get('message', '')}".rstrip()
    return f"[{event.type}]"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--bridge",
        default=None,
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
        default=None,
        help="model id (discovered via client.cursor.models() when empty)",
    )
    args = parser.parse_args()

    if not os.environ.get("CURSOR_API_KEY"):
        sys.exit("error: CURSOR_API_KEY must be set (create a key at https://cursor.com/dashboard)")

    try:
        with Client(workspace=args.workspace, bridge_bin=args.bridge) as client:
            version = client.version()
            print(f"bridge {version.bridge_version} protocol {version.protocol_version}")

            model = args.model
            if not model:
                models = client.cursor.models()
                if not models:
                    sys.exit("error: no models available to this account")
                model = models[0].id

            with client.agents.create(model=model, cwd=args.workspace) as agent:
                print(f"agent created: {agent.id} (model {agent.model})")
                run = agent.send(args.prompt)
                for event in run:
                    line = describe(event)
                    if line:
                        print(line)
                result = run.wait()
                print(f"run finished: status={result.status} duration={result.duration_ms}ms")
                if not result.ok:
                    print(f"error: {result.error_code or ''} {result.error_message or ''}".rstrip())
                elif result.text:
                    print(f"final result:\n{result.text}")
        print("bridge stopped")
    except CursorSdkError as err:
        sys.exit(f"error: {err}")


if __name__ == "__main__":
    main()
