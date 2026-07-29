"""The :class:`Agent` handle: send messages, manage lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._proto import agent_pb2, messages_pb2
from ._run import Run, RunResult, result_from_proto

if TYPE_CHECKING:
    from ._client import Client


class Agent:
    """One agent, local or resumed, identified by ``id``.

    Usable as a context manager; leaving the block closes the agent
    (releasing local resources without deleting durable state).
    """

    def __init__(self, client: Client, agent_id: str, model: str | None) -> None:
        self._client = client
        self.id = agent_id
        self.model = model

    def __repr__(self) -> str:
        return f"Agent(id={self.id!r}, model={self.model!r})"

    def send(
        self,
        text: str,
        *,
        enable_deltas: bool = False,
        enable_steps: bool = False,
    ) -> Run:
        """Send a user message and return the :class:`Run` streaming it.

        ``enable_deltas`` / ``enable_steps`` opt into raw interaction-update
        and completed-step events on the stream (see ``docs/streaming.md``).
        """
        request = agent_pb2.SendRequest(
            agent_id=self.id,
            message=messages_pb2.UserMessage(text=text),
            options=messages_pb2.SendOptions(
                enable_deltas=enable_deltas, enable_steps=enable_steps
            ),
        )
        stream = self._client._rpc.server_stream(
            "SdkAgentService", "Send", request, messages_pb2.RunStreamMessage
        )
        return Run(self._client, self.id, stream)

    def runs(self, limit: int = 0) -> list[RunResult]:
        """List this agent's runs as point-in-time snapshots."""
        response = self._client._rpc.unary(
            "SdkAgentService",
            "ListRuns",
            agent_pb2.ListRunsRequest(
                agent_id=self.id, options=agent_pb2.ListRunsOptions(limit=limit)
            ),
            agent_pb2.ListRunsResponse,
        )
        return [result_from_proto(snapshot) for snapshot in response.items]

    def close(self) -> None:
        """Release local resources. Durable state is kept."""
        self._unary("CloseAgent")

    def archive(self) -> None:
        self._unary("ArchiveAgent")

    def unarchive(self) -> None:
        self._unary("UnarchiveAgent")

    def delete(self) -> None:
        """Permanently delete the agent and its durable data."""
        self._unary("DeleteAgent")

    def _unary(self, method: str) -> None:
        request_type = getattr(agent_pb2, f"{method}Request")
        response_type = getattr(agent_pb2, f"{method}Response")
        self._client._rpc.unary(
            "SdkAgentService", method, request_type(agent_id=self.id), response_type
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
