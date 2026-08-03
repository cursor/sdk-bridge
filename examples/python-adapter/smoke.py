#!/usr/bin/env python3
"""Offline protocol smoke test: exercise every RPC that works without a
Cursor API key against a real bridge binary.

This is the CI gate that runs the example adapter against each release's
standalone archive (see .github/workflows/example-smoke.yml). It needs no
network and no CURSOR_API_KEY, so it can't run a real turn — demo.py covers
that when a key is available — but it does exercise the spawn/handshake,
bearer auth, Ping/GetVersion, the Milestone-4 CreateAgent shape (explicit
``local.cwd``), agent management, and Shutdown.

Usage: python smoke.py [--bridge <path>] [--workspace <dir>]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

from cursor_adapter import Client


def check(condition: bool, label: str) -> None:
    if not condition:
        sys.exit(f"FAIL: {label}")
    print(f"ok: {label}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--bridge", default=None, help="bridge executable path")
    parser.add_argument("--workspace", default=None, help="workspace directory")
    args = parser.parse_args()

    workspace = args.workspace or tempfile.mkdtemp(prefix="sdk-bridge-smoke-")
    # The smoke must behave the same with or without a key in the caller's
    # environment; scrub it so CI results are reproducible.
    os.environ.pop("CURSOR_API_KEY", None)

    with Client(workspace=workspace, bridge_bin=args.bridge) as client:
        check(client.ping() == "pong", "Ping")

        version = client.version()
        check(version.protocol_version == "sdk.v1", f"GetVersion protocol ({version.protocol_version})")
        check("agent.send" in version.capabilities, "GetVersion capabilities include agent.send")

        # The Milestone 4 CreateAgent shape: explicit model + local.cwd.
        # This is the exact request the README tells adapter authors to send
        # first; it must work against every release.
        agent = client.agents.create(model="composer-2", cwd=workspace)
        check(bool(agent.id), f"CreateAgent with local.cwd ({agent.id})")

        info = client.agents.get(agent.id)
        check(info.id == agent.id, "GetAgent")

        listed = client.agents.list()
        check(any(item.id == agent.id for item in listed), "ListAgents contains the new agent")

        resumed = client.agents.resume(agent.id)
        check(resumed.id == agent.id, "ResumeAgent")

        agent.close()
        print("ok: CloseAgent")
    print("ok: Shutdown (client closed)")
    print("smoke test passed")


if __name__ == "__main__":
    main()
