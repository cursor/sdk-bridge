# Run streaming semantics

Two RPCs return a server stream of `RunStreamMessage` (defined in
`sdk_messages.proto`):

- `SdkAgentService.Send` — the **live** stream for a new turn. Streams events
  as the run executes and ends when the run reaches a terminal status.
- `SdkAgentService.ObserveRun` — the **durable** stream for an existing run.
  Replays recorded events from the beginning (or from a resume offset) and
  then follows the live run if it is still executing.

## The envelope

```protobuf
message RunStreamMessage {
  oneof envelope {
    SdkMessage sdk_message = 1;          // high-level conversation messages
    RunStreamResult result = 2;          // terminal status + RunResult
    RunStreamDone done = 3;              // end-of-stream marker
    InteractionUpdate interaction_update = 5; // raw deltas (opt-in)
    ConversationStep step = 6;           // completed steps (opt-in)
  }
  optional string offset = 4;            // opaque resume token
}
```

- `sdk_message` — typed conversation events. `type` is a string discriminator
  and `message` a JSON object (`google.protobuf.Struct`); the payload shapes
  match the public SDK's message types. Adapters should switch on `type` and
  ignore unknown values.
- `interaction_update` — raw streaming deltas (for example text deltas).
  Only emitted when `SendOptions.enable_deltas` is true.
- `step` — completed conversation steps. Only emitted when
  `SendOptions.enable_steps` is true.
- `result` — the run reached a terminal state. Carries the
  `RunLifecycleStatus` (`FINISHED`, `ERROR`, `CANCELLED`, `EXPIRED`), an
  optional `error_code`, and the full `RunResult` (final assistant text,
  model, duration, git info, usage).
- `done` — the last message; the stream closes normally afterwards.

A normal live stream is therefore:

```text
sdk_message* (interaction_update | step | sdk_message)* result done
```

## Keepalives and unknown envelopes

Long tool executions or thinking pauses can leave the stream idle, and
intermediaries may kill idle connections. The bridge emits a keepalive after
~15 seconds of idle time: a `RunStreamMessage` with **no envelope case set and
no offset**.

Rules for adapters:

- A message with no recognized envelope case is a no-op. Ignore it silently.
- Never treat an empty envelope as an error or as end-of-stream.
- This is also the general forward-compatibility rule: new envelope cases may
  be added to `sdk.v1`, and adapters must skip cases they do not understand.

## Offsets and resume

Messages that originate from durable run events carry an `offset` — an opaque,
**exclusive** resume token scoped to a single run. To resume after a
disconnect:

1. Track the last non-empty `offset` you processed.
2. Call `ObserveRun` with `run_id` and `after_offset` set to that value. The
   stream continues with the first event *after* the offset.
3. With `after_offset` unset, `ObserveRun` replays from the beginning.

Keepalive frames carry no offset and must not advance your bookkeeping. Do not
compare or order offsets — they are opaque and only valid for the run that
produced them.

Treat offsets as scoped to the **stream kind** as well as the run: resume
`ObserveRun` only with offsets you observed from `ObserveRun` itself. Live
`Send` streams can interleave non-durable events (status updates, deltas,
steps) into their numbering, so a live offset may not correspond to the same
position in the durable event log — current bridges can skip events if you
pass one to `after_offset`. When recovering from a dropped `Send` stream,
replay `ObserveRun` from the beginning (or from the last offset a *previous
`ObserveRun`* gave you) and de-duplicate on your side.

## Ending a stream

- **Run completes** — you receive `result`, then `done`, then a normal stream
  close. Anything else (a transport error, a Connect error) means the stream
  failed and the run may still be executing; recover with `ObserveRun`,
  `WaitLiveRun`, or `GetRun`.
- **Cancellation** — call `CancelRun`. The stream still delivers a terminal
  `result` (status `CANCELLED`) followed by `done`.
- **Client disconnect** — dropping the `Send` stream does *not* cancel the
  run. Reconnect with `ObserveRun`, or cancel explicitly.

## Non-streaming alternatives

Adapters that do not need incremental output can:

- call `WaitLiveRun(run_id)` to block until the run is terminal and get its
  `RunResult`; or
- poll `GetRun(run_id)` for point-in-time `RunSnapshot`s.

To learn the `run_id` early, read it from the first `sdk_message` on the
`Send` stream — the payloads carry `agent_id` and `run_id` fields (the run
typically opens with a `system` message of subtype `init`) — or list runs for
the agent.

## Common `sdk_message` types

`SdkMessage.type` mirrors the public SDK's message stream. Types you will see
today include `system` (run start, subtype `init`), `assistant` (content
blocks with `text` / `tool_use`), `user`, `tool_call` (status `running` /
`completed` / `error`), `thinking`, `status`, `task`, and `usage`. Payload
shapes match the [`@cursor/sdk` documentation](https://cursor.com/docs/sdk);
switch on `type` and ignore unknown ones.

Two `status` details worth knowing: its payload carries the lifecycle
`status` plus `agent_id` / `run_id`, and when a run fails the human-readable
failure text arrives in the *status* payload's `message` field — the terminal
`RunStreamResult.error_code` can be empty for such failures, so surface the
last `status` message when reporting errors.
