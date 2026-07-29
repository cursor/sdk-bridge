"""The streaming surface: the :class:`Run` handle.

One ``Agent.send`` produces one :class:`Run`. Iterating the run yields
:class:`RunEvent` values (the envelope handling from ``docs/streaming.md``:
keepalives and unknown envelope cases are skipped silently); ``wait()``
drains to the terminal :class:`RunResult`; ``iter_text()`` yields assistant
text as it streams. A dropped live stream does **not** cancel the run —
``wait()`` falls back to ``WaitLiveRun`` and ``observe()`` replays durable
events via ``ObserveRun``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from google.protobuf import json_format

from ._errors import CursorSdkError, RpcError, TransportError
from ._proto import agent_pb2, messages_pb2

if TYPE_CHECKING:
    from ._client import Client


@dataclass
class RunEvent:
    """One event from a run stream.

    ``type`` is the payload discriminator (``system``, ``assistant``,
    ``tool_call``, ``status``, ... — plus delta/step types when enabled on
    send) and ``payload`` the JSON object that came with it. ``offset`` is
    the opaque resume token, present only on durable events.
    """

    type: str
    payload: dict[str, Any]
    offset: str | None = None


@dataclass
class RunResult:
    """Terminal outcome of a run.

    A failed run is still a *successful* stream (see ``docs/errors.md``):
    inspect ``status`` — and ``error_message``, which carries the
    human-readable failure text from the last ``status`` event, since
    ``error_code`` can be empty for model-side failures.
    """

    run_id: str
    agent_id: str
    status: str  # "finished" | "error" | "cancelled" | "expired"
    text: str
    model: str | None = None
    duration_ms: int = 0
    error_code: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "finished"


def _status_name(status: int) -> str:
    try:
        name = messages_pb2.RunLifecycleStatus.Name(status)
    except ValueError:
        return str(status)
    return name.removeprefix("RUN_LIFECYCLE_STATUS_").lower()


def result_from_proto(
    proto: messages_pb2.RunResult | messages_pb2.RunSnapshot,
    error_code: str | None = None,
    error_message: str | None = None,
) -> RunResult:
    return RunResult(
        run_id=proto.run_id,
        agent_id=proto.agent_id,
        status=_status_name(proto.status),
        text=proto.result,
        model=proto.model.id or None,
        duration_ms=proto.duration_ms,
        error_code=error_code,
        error_message=error_message,
    )


class Run:
    """Handle for one agent turn, wrapping the live ``Send`` stream."""

    def __init__(
        self,
        client: Client,
        agent_id: str,
        stream: Iterator[messages_pb2.RunStreamMessage],
    ) -> None:
        self._client = client
        self.agent_id = agent_id
        #: Populated from the first stream event that carries a run_id.
        self.run_id: str | None = None
        #: Last offset seen on the live stream. Live offsets can interleave
        #: non-durable events, so resume observe() only with offsets from a
        #: previous observe() (see docs/streaming.md), never with this one.
        self.last_offset: str | None = None
        self.result: RunResult | None = None
        self._stream: Iterator[messages_pb2.RunStreamMessage] | None = stream
        self._stream_error: CursorSdkError | None = None
        self._last_status_message = ""

    def __iter__(self) -> Iterator[RunEvent]:
        return self.events()

    def events(self) -> Iterator[RunEvent]:
        """Yield events from the live stream until the run completes.

        Stream failures are recorded before propagating so a later
        ``wait()`` can still recover the terminal result server-side.
        """
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            for message in stream:
                event = self._ingest(message, track_offset=True)
                if event is not None:
                    yield event
                if message.WhichOneof("envelope") == "done":
                    return
        except (TransportError, RpcError) as err:
            self._stream_error = err
            raise

    def _ingest(
        self, message: messages_pb2.RunStreamMessage, track_offset: bool
    ) -> RunEvent | None:
        offset = message.offset if message.HasField("offset") else None
        if offset and track_offset:
            self.last_offset = offset
        case = message.WhichOneof("envelope")
        if case == "sdk_message":
            kind = message.sdk_message.type
            payload = json_format.MessageToDict(message.sdk_message.message)
            if self.run_id is None and payload.get("run_id"):
                self.run_id = payload["run_id"]
            if kind == "status" and payload.get("message"):
                # On failure the human-readable reason arrives here, not in
                # the terminal result's error_code.
                self._last_status_message = payload["message"]
            return RunEvent(type=kind, payload=payload, offset=offset)
        if case == "interaction_update":
            update = message.interaction_update
            return RunEvent(
                type=update.type, payload=json_format.MessageToDict(update.update), offset=offset
            )
        if case == "step":
            step = message.step
            return RunEvent(
                type=step.type, payload=json_format.MessageToDict(step.step), offset=offset
            )
        if case == "result":
            result = message.result
            self.run_id = self.run_id or result.run_id
            self.result = result_from_proto(
                result.result,
                error_code=result.error_code if result.HasField("error_code") else None,
                error_message=self._last_status_message or None,
            )
            # Older payloads may leave RunResult ids unset; the envelope has them.
            self.result.run_id = self.result.run_id or result.run_id
            self.result.agent_id = self.result.agent_id or result.agent_id
            if self.result.status == "unspecified":
                self.result.status = _status_name(result.status)
            return None
        # done, keepalives (no envelope case), and unknown future cases.
        return None

    def iter_text(self) -> Iterator[str]:
        """Yield assistant text blocks as they stream."""
        for event in self.events():
            if event.type != "assistant":
                continue
            for block in event.payload.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    yield block["text"]

    def wait(self) -> RunResult:
        """Block until the run is terminal and return its result.

        Drains any unconsumed live events. A dropped or failed live stream
        does not cancel the run, so whenever the stream ended without a
        terminal result — including when a caller's own iteration already
        raised — this falls back to ``WaitLiveRun``.
        """
        if self.result is None:
            try:
                for _ in self.events():
                    pass
            except (TransportError, RpcError):
                pass  # Recorded by events(); recovered below when possible.
        if self.result is None:
            if self.run_id is None:
                # Nothing to recover with; surface what broke the stream.
                raise self._stream_error or TransportError(
                    "run stream ended without a terminal result or run_id"
                )
            response = self._client._rpc.unary(
                "SdkAgentService",
                "WaitLiveRun",
                agent_pb2.WaitLiveRunRequest(run_id=self.run_id),
                agent_pb2.WaitLiveRunResponse,
                timeout=None,
            )
            self.result = result_from_proto(
                response.result, error_message=self._last_status_message or None
            )
        return self.result

    def text(self) -> str:
        """Block until the run is terminal and return the final assistant text."""
        return self.wait().text

    def cancel(self) -> None:
        """Request cancellation; the stream still delivers a terminal result."""
        self._client._rpc.unary(
            "SdkAgentService",
            "CancelRun",
            agent_pb2.CancelRunRequest(run_id=self._require_run_id(), agent_id=self.agent_id),
            agent_pb2.CancelRunResponse,
        )

    def observe(self, after_offset: str | None = None) -> Iterator[RunEvent]:
        """Replay the run's durable events via ``ObserveRun``.

        With ``after_offset`` unset, replays from the beginning and follows
        the run if it is still executing. Resume only with an ``offset``
        taken from an event a previous ``observe()`` yielded.
        """
        request = agent_pb2.ObserveRunRequest(run_id=self._require_run_id())
        if after_offset is not None:
            request.after_offset = after_offset
        stream = self._client._rpc.server_stream(
            "SdkAgentService", "ObserveRun", request, messages_pb2.RunStreamMessage
        )
        for message in stream:
            event = self._ingest(message, track_offset=False)
            if event is not None:
                yield event
            if message.WhichOneof("envelope") == "done":
                return

    def _require_run_id(self) -> str:
        if self.run_id is None:
            raise CursorSdkError(
                "run_id is not known yet; it arrives with the first stream event"
            )
        return self.run_id
