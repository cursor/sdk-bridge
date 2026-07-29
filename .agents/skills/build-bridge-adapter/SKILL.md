---
name: build-bridge-adapter
description: Build a full Cursor SDK for a new language on top of the sdk.v1 bridge protocol — codegen, managed bridge lifecycle, client/agent/run API design, streaming, errors, and the adapter-side callback services. Use when asked to build, port, extend, or debug an adapter/SDK/client for the Cursor SDK bridge in any language.
---

# Build a Cursor SDK for a new language

An *adapter* spawns `cursor-sdk-bridge` and speaks the `sdk.v1` Connect
protocol to it. The end state of this skill is not a demo script but a real
SDK: a library another developer can install and use to script Cursor agents
without knowing the bridge exists. Read `docs/protocol.md` first;
`examples/python-adapter/` is a working miniature of the target shape — one
module per architecture-table component, over a hand-rolled transport that
keeps the wire format visible — and `docs/streaming.md` / `docs/errors.md`
cover streams and failures.

## The target architecture

Cursor's official SDKs converge on the same shape. Aim for it, adapted to
your language's idioms:

| Component | Responsibility |
| --- | --- |
| **Bridge manager** | Locate the bridge (env override → bundled/downloaded archive), spawn it, perform the ready-line handshake, expose the endpoint, shut it down (RPC → wait → kill). One managed bridge per client, created lazily on first use; also allow attaching to an externally supplied endpoint. |
| **Transport** | Connect-over-HTTP/1.1 client: unary POSTs and server-stream framing, bearer auth on every request, translation of Connect errors into your error types. Generated stubs or hand-rolled (see `examples/python-adapter/`). |
| **`Client`** | Owns the bridge manager + transport. Typed low-level methods mirroring `SdkAgentService` (`create_agent`, `send`, `wait_live_run`, `observe_run`, `cancel_run`, `list_agents`, ...). Everything else builds on it. |
| **`Agent` handle** | `create(options)` / `resume(id)` / `get` / `list` constructors; `send(message) -> Run`; `close`, `archive`, `delete`; custom-tool registration. Holds `agent_id` + model. |
| **`Run` handle** | The streaming surface: iterate events; convenience accessors (assistant text iterator, blocking `wait()` → result, terminal `text()`); `observe(after_offset)` for resume; `cancel()`. Tracks the last seen `offset`. |
| **`Cursor` catalog** | `me()`, `models()`, `repositories()` from `SdkCursorService`. |
| **Errors** | One base error plus a taxonomy mapped from Connect codes + `SdkErrorDetails.sdk_error_code` (auth, not-found, rate-limit, busy, validation, ...). Preserve `request_id`, `retry_after`, `rate_limit` on the error object. |
| **Callback servers** | Optional loopback Connect servers implementing `SdkCustomToolCallbackService` and `SdkStoreCallbackService`, so users can define tools and stores in your language. |

A north-star usage sketch (translate to your language):

```python
client = Client()                       # spawns/attaches the bridge lazily
agent = client.agents.create(model="composer-2", local={"cwd": ["/repo"]})
run = agent.send("Summarize this repository.")
for text in run.iter_text():
    print(text)
result = run.wait()
agent.close()
client.close()                          # shuts the bridge down
```

Plus a one-liner for the simplest case (`prompt(...)`: create → send → wait →
close) and a context-manager/`defer`/RAII form so the bridge can never leak.

## Prerequisites and constraints

- The target language needs (a) a protobuf runtime and (b) an HTTP/1.1
  client. A [Connect](https://connectrpc.com/docs/) client library is ideal
  but not required — every RPC is
  `POST http://<host>:<port>/sdk.v1.<Service>/<Method>` with a protobuf
  (`content-type: application/proto`) or JSON (`application/json`) body.
  **Classic gRPC will not work**: the bridge serves HTTP/1.1 only.
- `proto/sdk/v1/` must exist at the repo root (it is synced on releases; pin
  a `vX.Y.Z` tag). Never edit anything under `proto/` — it is generated.
- Running a real turn needs a `CURSOR_API_KEY`
  ([cursor.com/dashboard](https://cursor.com/dashboard)).

Work through the milestones below **in order**, and keep a runnable
demo/test at every milestone — each one builds on a verified previous layer.

## Milestone 1 — Codegen

Copy `examples/python-adapter/buf.gen.yaml` as a template: keep
`inputs: [directory: ../../proto]` and swap the plugins for the target
language's protobuf + Connect plugins. For compiled languages, use buf's
`managed` mode to override language package options — the synced protos carry
Cursor-internal values for `go_package`, `java_package`, and friends. Only
`sdk/v1/*.proto` and Google well-known types are involved; no other
dependencies. Commit the `buf.gen.yaml`, gitignore the `gen/` output.

If the language has no Connect plugin, generate plain protobuf messages and
hand-write the tiny HTTP layer (unary = one POST; server streams = the
Connect streaming envelope: 1-byte flags + 4-byte big-endian length frames,
end-of-stream flag `0x02` carrying a JSON EndStreamResponse with any error).
`examples/python-adapter/cursor_adapter/_transport.py` does exactly this in
~100 lines.

## Milestone 2 — Bridge manager

- Locate the bridge: an env override such as `CURSOR_SDK_BRIDGE_BIN` first,
  then your package's bundled/downloaded archive
  (`https://downloads.cursor.com/sdk-bridge/<version>/<os>/<arch>/cursor-sdk-bridge-package.tar.gz`,
  os: `linux|darwin|win32`, arch: `x64|arm64`; launcher at
  `cursor-sdk-bridge/bin/cursor-sdk-bridge`, `.cmd` on Windows).
- Spawn with `CURSOR_API_KEY` in the environment, `--workspace <dir>` for
  local agents, and `CURSOR_SDK_CLIENT_LANGUAGE=<language>` for attribution.
- Handshake: capture **stderr**, scan for the literal prefix
  `cursor-sdk-bridge ready ` (trailing space), parse the JSON after it,
  validate `schemaVersion == 1`, `transport == "tcp"`,
  `protocol == "connect"`, ignore unknown fields. Apply a ~30s startup
  timeout; if the process exits first, surface its captured stderr. Keep
  draining stderr forever afterwards — a full pipe blocks the bridge. Never
  log the raw discovery line (older bridges inline the token).
- Read the bearer token from the `authTokenFile` path, trimmed.
- Shutdown: `SdkBridgeControlService.Shutdown` (or SIGTERM), wait ~5s, then
  kill. Make this run on client close *and* on interpreter/process exit so a
  crashed caller cannot leak bridges.
- Support attaching to an already-running bridge (explicit URL + token) —
  useful for tests and for hosts that manage the process themselves.

## Milestone 3 — Transport, auth, and errors

- Send `Authorization: Bearer <token>` on **every** request — unary *and*
  streaming (a common bug: interceptor APIs often cover only unary).
  Missing/wrong token ⇒ `UNAUTHENTICATED`.
- Verify with `SdkBridgeControlService.Ping`, then `GetVersion` (expect
  `protocol_version == "sdk.v1"`; capabilities gate optional features).
- Build the error layer now, not last: decode `sdk.v1.SdkErrorDetails` from
  failed RPCs (`docs/errors.md` has the taxonomy and wire encoding) and map
  `sdk_error_code` + Connect code onto your language's exception/error
  hierarchy. Expose the full `request_id`, `retry_after`, and `rate_limit`.
  Parse protobuf JSON with unknown-field tolerance everywhere.

## Milestone 4 — First turn: `Agent.send` → `Run`

1. `SdkAgentService.CreateAgent` with `options.local.cwd = ["<workspace>"]`
   and an explicit `options.model` — local agents require one; discover IDs
   via `SdkCursorService.ListModels` (catalog calls **require** a per-call
   `api_key`; there is no env fallback).
2. `SdkAgentService.Send` with the `agent_id` and a `UserMessage{text}`;
   wrap the server stream in your `Run` handle per `docs/streaming.md`:
   - dispatch on the `envelope` oneof; **ignore** messages with no case set
     (keepalives) and unknown cases;
   - `sdk_message`: dispatch on `type` (`system`, `assistant`, `tool_call`,
     `status`, ...); payloads are JSON objects (`google.protobuf.Struct`).
     On failure the human-readable reason arrives in the `status` payload's
     `message` — surface it, since `RunStreamResult.error_code` can be empty;
   - track the last non-empty `offset`; `result` then `done` end the run;
   - a dropped stream does **not** cancel the run — `Run.observe()` resumes
     via `ObserveRun` + `after_offset` (only pass offsets that came from
     `ObserveRun` itself; live `Send` offsets are a different numbering — see
     `docs/streaming.md`), and `wait()` falls back to `WaitLiveRun`.
3. Layer the conveniences on the raw event stream: assistant-text iterator,
   blocking `wait()`, terminal `text()`, `cancel()`.

## Milestone 5 — Management surface and catalog

Fill out the rest of `SdkAgentService` on `Client`/`Agent`: `ResumeAgent`,
`GetAgent`/`ListAgents` (pagination cursors), `ArchiveAgent`/`Unarchive`/
`Delete`/`Close`, `ListRuns`/`GetRun`/`GetRunConversation`,
`ListAgentMessages`, artifacts (`ListArtifacts` + chunked
`DownloadArtifact`), `GetUsage` (cloud only), and the `Cursor` catalog
(`Me`, `ListModels`, `ListRepositories`). These are mechanical once
milestones 1–4 work.

## Milestone 6 — Callback services (custom tools / stores)

These invert direction: the SDK runs a loopback Connect **server** and the
bridge authenticates to it with a bearer token the SDK chooses. Validate that
token on every callback, exactly as the bridge validates yours. Gotchas that
cost real debugging time (details in `docs/services.md`): callback POSTs may
use chunked transfer-encoding (decode it — minimal HTTP servers often don't);
store outputs must be the bare record, not the wrapped input envelope; tool
results are `Struct`s, so scalar returns need wrapping in an object.

- **Custom tools** — implement `SdkCustomToolCallbackService.CallCustomTool`
  (execute the named user function with the Struct args, return a Struct
  result). Declare tool metadata in `LocalAgentOptions.custom_tools` on
  CreateAgent; register the server via
  `--tool-callback-url`/`--tool-callback-auth-token` or
  `SdkBridgeControlService.SetToolCallback`. Design the user-facing API as
  "register a function with a schema", not "implement an RPC service".
- **Custom stores** — implement `SdkStoreCallbackService.CallStore`
  (substores `agents|runs|runEvents|checkpoints`; methods
  `get|create|update|delete|list|append`; checkpoint blobs are base64).
  Launch the bridge with `LocalAgentStoreConfig{type:"custom"}` (e.g.
  `--local-store '{"type":"custom"}'`) plus
  `--store-callback-url`/`--store-callback-auth-token` (launch-time only).

## Verification checklist

Functional (run against a real bridge):

- [ ] Handshake: ready line parsed, token read from file, `Ping` succeeds;
      startup timeout and exit-before-ready both produce useful errors.
- [ ] A request without `Authorization` fails with `UNAUTHENTICATED`, and it
      maps to your auth error type.
- [ ] One full turn through the public API (`client → agent → run`): stream
      yields events, terminal result observed, against a real
      `CURSOR_API_KEY`.
- [ ] Keepalive frames (empty envelope) are ignored; a >15s tool pause does
      not break the stream; unknown envelope cases and `SdkMessage.type`s are
      skipped silently.
- [ ] Bridge exits cleanly on client close; killed on timeout; no orphan
      process after the host program exits or crashes.
- [ ] Failed RPCs surface `sdk_error_code` and the full `request_id`.

API quality (review against the architecture table):

- [ ] A newcomer can run one prompt in ≤5 lines without touching proto types.
- [ ] Raw proto/transport types do not leak into the public API surface.
- [ ] `Run` supports both incremental consumption and fire-and-`wait()`.
- [ ] Errors are catchable by class, not by string matching.
- [ ] The bridge process is invisible in the happy path and controllable
      (endpoint attach, custom binary path) when needed.
