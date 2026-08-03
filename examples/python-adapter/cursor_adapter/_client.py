"""The :class:`Client`: entry point owning the bridge manager and transport.

The bridge process is invisible in the happy path — it is spawned lazily on
the first RPC and shut down by ``close()`` (or the context manager, or the
manager's ``atexit`` hook). Pass ``endpoint`` + ``auth_token`` to attach to
an already-running bridge instead, e.g. in tests or when the host process
manages the bridge itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

from ._agent import Agent
from ._bridge import BridgeManager
from ._errors import AuthenticationError, CursorSdkError
from ._proto import agent_pb2, control_pb2, cursor_pb2, messages_pb2
from ._transport import Transport


@dataclass
class BridgeVersion:
    bridge_version: str
    protocol_version: str
    capabilities: list[str] = field(default_factory=list)


@dataclass
class AgentInfo:
    """Metadata for one agent, from ``GetAgent`` / ``ListAgents``."""

    id: str
    name: str
    summary: str
    status: str  # "running" | "finished" | "error" | ...
    archived: bool
    created_at: datetime | None = None


@dataclass
class Model:
    id: str
    display_name: str
    description: str


@dataclass
class User:
    """The authenticated account identity for an API key."""

    email: str
    first_name: str
    last_name: str
    api_key_name: str


def _agent_info(proto: messages_pb2.SdkAgentInfo) -> AgentInfo:
    try:
        status = messages_pb2.AgentInfoStatus.Name(proto.status)
        status = status.removeprefix("AGENT_INFO_STATUS_").lower()
    except ValueError:
        status = str(proto.status)
    return AgentInfo(
        id=proto.agent_id,
        name=proto.name,
        summary=proto.summary,
        status=status,
        archived=proto.archived,
        created_at=(
            proto.created_at.ToDatetime() if proto.HasField("created_at") else None
        ),
    )


class Client:
    """Owns one bridge (spawned lazily) and exposes the SDK surface.

    - ``client.agents`` — create / resume / get / list agents.
    - ``client.cursor`` — account catalog (``me``, ``models``,
      ``repositories``); requires an API key per call.
    - ``client.ping()`` / ``client.version()`` — bridge health and features.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        workspace: str | None = None,
        bridge_bin: str | None = None,
        endpoint: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        if (endpoint is None) != (auth_token is None):
            raise ValueError("endpoint and auth_token must be provided together")
        self.api_key = api_key or os.environ.get("CURSOR_API_KEY", "")
        self.workspace = os.path.abspath(workspace or os.getcwd())
        self._bridge_bin = bridge_bin
        self._manager: BridgeManager | None = None
        self._transport: Transport | None = (
            Transport(endpoint, auth_token) if endpoint and auth_token else None
        )
        self._closed = False
        self.agents = Agents(self)
        self.cursor = Cursor(self)

    @property
    def _rpc(self) -> Transport:
        """The transport, spawning the managed bridge on first use."""
        if self._closed:
            raise CursorSdkError("client is closed")
        if self._transport is None:
            self._manager = BridgeManager(
                binary=self._bridge_bin, workspace=self.workspace, api_key=self.api_key or None
            )
            endpoint = self._manager.start()
            self._transport = Transport(endpoint.url, endpoint.auth_token)
        return self._transport

    def ping(self) -> str:
        response = self._rpc.unary(
            "SdkBridgeControlService",
            "Ping",
            control_pb2.PingRequest(),
            control_pb2.PingResponse,
        )
        return response.message

    def version(self) -> BridgeVersion:
        response = self._rpc.unary(
            "SdkBridgeControlService",
            "GetVersion",
            control_pb2.GetVersionRequest(),
            control_pb2.GetVersionResponse,
        )
        return BridgeVersion(
            bridge_version=response.bridge_version,
            protocol_version=response.protocol_version,
            capabilities=list(response.capabilities),
        )

    def close(self) -> None:
        """Shut the managed bridge down: ``Shutdown`` RPC, then wait/kill."""
        if self._closed:
            return
        self._closed = True
        transport, self._transport = self._transport, None
        manager, self._manager = self._manager, None
        if transport is not None and manager is not None:
            try:
                transport.unary(
                    "SdkBridgeControlService",
                    "Shutdown",
                    control_pb2.ShutdownRequest(),
                    control_pb2.ShutdownResponse,
                    timeout=5.0,
                )
            except CursorSdkError:
                pass  # The manager escalates to SIGTERM / kill below.
        if manager is not None:
            manager.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class Agents:
    """``client.agents``: agent constructors and listings."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        cwd: str | list[str] | None = None,
        name: str | None = None,
    ) -> Agent:
        """Create a local agent working in ``cwd`` (default: the client
        workspace). Local agents require an explicit ``model`` — discover
        IDs via ``client.cursor.models()``."""
        cwds = [cwd] if isinstance(cwd, str) else list(cwd or [self._client.workspace])
        options = messages_pb2.AgentOptions(
            model=messages_pb2.ModelSelection(id=model),
            name=name or "",
            local=messages_pb2.LocalAgentOptions(cwd=[os.path.abspath(path) for path in cwds]),
            # Always set the key on AgentOptions. Do not rely on the bridge's
            # CURSOR_API_KEY env var alone: not every operation falls back to
            # it on every bridge build, and runs on an agent created without
            # an explicit api_key can fail with "Invalid User API Key".
            api_key=self._client.api_key,
        )
        response = self._client._rpc.unary(
            "SdkAgentService",
            "CreateAgent",
            agent_pb2.CreateAgentRequest(options=options),
            agent_pb2.CreateAgentResponse,
        )
        return Agent(self._client, response.agent_id, response.model.id or model)

    def resume(self, agent_id: str, *, model: str | None = None) -> Agent:
        """Re-attach to an existing agent, optionally switching model."""
        options = messages_pb2.AgentOptions(api_key=self._client.api_key)
        if model:
            options.model.id = model
        response = self._client._rpc.unary(
            "SdkAgentService",
            "ResumeAgent",
            agent_pb2.ResumeAgentRequest(agent_id=agent_id, options=options),
            agent_pb2.ResumeAgentResponse,
        )
        return Agent(self._client, response.agent_id, response.model.id or model)

    def get(self, agent_id: str) -> AgentInfo:
        response = self._client._rpc.unary(
            "SdkAgentService",
            "GetAgent",
            agent_pb2.GetAgentRequest(agent_id=agent_id),
            agent_pb2.GetAgentResponse,
        )
        return _agent_info(response.agent)

    def list(self, *, limit: int = 0, include_archived: bool = False) -> list[AgentInfo]:
        """List agents, following pagination cursors until exhausted (or
        ``limit`` items when it is non-zero)."""
        items: list[AgentInfo] = []
        cursor = ""
        while True:
            options = agent_pb2.ListAgentsOptions(limit=limit, cursor=cursor)
            if include_archived:
                options.include_archived = True
            response = self._client._rpc.unary(
                "SdkAgentService",
                "ListAgents",
                agent_pb2.ListAgentsRequest(options=options),
                agent_pb2.ListAgentsResponse,
            )
            items.extend(_agent_info(item) for item in response.items)
            cursor = response.next_cursor
            if not cursor or (limit and len(items) >= limit):
                return items[:limit] if limit else items


class Cursor:
    """``client.cursor``: account and catalog calls (``SdkCursorService``).

    Catalog RPCs require a per-call API key — the bridge fails closed rather
    than falling back to its environment (see ``docs/services.md``).
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def _options(self) -> cursor_pb2.CursorRequestOptions:
        if not self._client.api_key:
            raise AuthenticationError(
                code="unauthenticated",
                message="catalog calls need an API key: pass Client(api_key=...) "
                "or set CURSOR_API_KEY",
                sdk_error_code="API_KEY_NOT_FOUND",
            )
        return cursor_pb2.CursorRequestOptions(api_key=self._client.api_key)

    def me(self) -> User:
        response = self._client._rpc.unary(
            "SdkCursorService",
            "Me",
            cursor_pb2.MeRequest(options=self._options()),
            cursor_pb2.MeResponse,
        )
        user = response.user
        return User(
            email=user.user_email,
            first_name=user.user_first_name,
            last_name=user.user_last_name,
            api_key_name=user.api_key_name,
        )

    def models(self) -> list[Model]:
        response = self._client._rpc.unary(
            "SdkCursorService",
            "ListModels",
            cursor_pb2.ListModelsRequest(options=self._options()),
            cursor_pb2.ListModelsResponse,
        )
        return [
            Model(id=item.id, display_name=item.display_name, description=item.description)
            for item in response.items
        ]

    def repositories(self) -> list[str]:
        response = self._client._rpc.unary(
            "SdkCursorService",
            "ListRepositories",
            cursor_pb2.ListRepositoriesRequest(options=self._options()),
            cursor_pb2.ListRepositoriesResponse,
        )
        return [item.url for item in response.items]
