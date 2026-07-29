"""cursor_adapter — an example Python SDK for the Cursor SDK bridge.

A miniature of the architecture in the ``build-bridge-adapter`` skill
(``.agents/skills/build-bridge-adapter/SKILL.md``): a bridge manager, a
hand-rolled Connect-over-HTTP/1.1 transport, ``Client`` / ``Agent`` /
``Run`` handles, the ``Cursor`` catalog, and a class-based error taxonomy.
The bridge process never appears in the happy path::

    from cursor_adapter import Client

    with Client() as client:                      # spawns the bridge lazily
        agent = client.agents.create(model="composer-2", cwd="/repo")
        run = agent.send("Summarize this repository.")
        for text in run.iter_text():
            print(text)
        result = run.wait()

or, for the simplest case, the one-liner::

    from cursor_adapter import prompt

    print(prompt("Summarize this repository.", cwd="/repo"))
"""

from __future__ import annotations

from ._agent import Agent
from ._bridge import BridgeEndpoint, BridgeManager
from ._client import AgentInfo, BridgeVersion, Client, Model, User
from ._errors import (
    AgentBusyError,
    AuthenticationError,
    BridgeProcessError,
    CursorSdkError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    RateLimitInfo,
    RpcError,
    TransportError,
    ValidationError,
)
from ._run import Run, RunEvent, RunResult

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "AgentBusyError",
    "AgentInfo",
    "AuthenticationError",
    "BridgeEndpoint",
    "BridgeManager",
    "BridgeProcessError",
    "BridgeVersion",
    "Client",
    "CursorSdkError",
    "InvalidStateError",
    "Model",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "RateLimitInfo",
    "RpcError",
    "Run",
    "RunEvent",
    "RunResult",
    "TransportError",
    "User",
    "ValidationError",
    "prompt",
]


def prompt(
    text: str,
    *,
    model: str | None = None,
    cwd: str | None = None,
    api_key: str | None = None,
    bridge_bin: str | None = None,
) -> str:
    """One-shot convenience: create an agent, send ``text``, return the
    final assistant text, and clean everything up.

    With ``model`` unset, the first model from the account's catalog is
    used (which requires an API key, like all catalog calls).
    """
    with Client(api_key=api_key, workspace=cwd, bridge_bin=bridge_bin) as client:
        if model is None:
            models = client.cursor.models()
            if not models:
                raise CursorSdkError("no models available to this account")
            model = models[0].id
        with client.agents.create(model=model, cwd=cwd) as agent:
            return agent.send(text).text()
