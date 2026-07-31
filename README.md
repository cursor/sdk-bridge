# Cursor SDK Bridge

The public home of the **Cursor SDK bridge protocol**: the stable `sdk.v1`
protobuf contract that lets you drive [Cursor agents](https://cursor.com/docs)
from any language, without depending on the TypeScript
([`@cursor/sdk`](https://www.npmjs.com/package/@cursor/sdk)) or Python
([`cursor-sdk`](https://pypi.org/project/cursor-sdk/)) SDKs directly.

The bridge is a small local server that embeds the TypeScript SDK
(`@cursor/sdk`) as a library and exposes its full surface — creating agents,
sending messages, streaming runs, custom tools, artifacts — over
[Connect](https://connectrpc.com/)/gRPC-Web using the protobuf definitions in
this repository. An *adapter* is anything that spawns
the bridge and speaks `sdk.v1` to it: an SDK for a new language, a service
integration, or a one-off script.

```text
┌────────────────-┐  spawn + Connect RPCs   ┌────────────────────┐   HTTPS    ┌─────────────┐
│  your adapter   │ ──────────────────────► │  cursor-sdk-bridge │ ─────────► │ Cursor API  │
│  (any language) │ ◄────────────────────── │  (local process)   │            │             │
└────────────────-┘  callback RPCs (tools,  └────────────────────┘            └─────────────┘
                     custom stores)
```

## How to use this repository

- **Scripting agents from TypeScript or Python?** Use the official SDKs —
  [`@cursor/sdk`](https://www.npmjs.com/package/@cursor/sdk) on npm or
  [`cursor-sdk`](https://pypi.org/project/cursor-sdk/) on PyPI. You do not
  need this repository.
- **Building an adapter for another language?** Pin the latest release of
  this repository (the newest
  [`vX.Y.Z` tag](https://github.com/cursor/sdk-bridge/tags)), generate code
  from `proto/sdk/v1/` with [buf](https://buf.build), and follow the
  protocol guide in [`docs/`](docs/). `examples/python-adapter/` is a
  complete working reference.
- **Using a Cursor agent to build the adapter?** Point it at this repository
  and tell it to follow [**Agent: start here**](#agent-start-here) below — a
  complete milestone-by-milestone build plan.

## Repository layout

| Path | Contents |
| --- | --- |
| `proto/sdk/v1/` | The `sdk.v1` protobuf contract. **Generated — do not edit.** Regenerated automatically on every SDK release. |
| `proto/manifest.json` | Release metadata: `protocol` (`"sdk.v1"`), `sdkVersion`, and the source commit. |
| `docs/` | The protocol guide: lifecycle, services, streaming, errors, versioning. |
| `examples/` | Minimal adapters in other languages, each with its own `buf.gen.yaml`. |

> **Note:** `proto/` is owned by Cursor's release automation and rewritten on
> every release, and every release pushes an annotated tag `vX.Y.Z` matching
> the released `@cursor/sdk` npm / `cursor-sdk` PyPI version. Pull requests
> must never touch `proto/`. If the directory is missing, the first release
> has not been published yet.

## The contract at a glance

Seven files under `proto/sdk/v1/`, package `sdk.v1`, self-contained apart from
Google well-known types:

| File | Role |
| --- | --- |
| `sdk_agent_service.proto` | `SdkAgentService` — create/resume agents, send messages, stream runs, artifacts, usage. |
| `sdk_cursor_service.proto` | `SdkCursorService` — client-level operations (identity, models, repositories). |
| `sdk_bridge_control_service.proto` | `SdkBridgeControlService` — bridge lifecycle (ping, version, shutdown). |
| `sdk_custom_tool_callback_service.proto` | `SdkCustomToolCallbackService` — implemented **by the adapter**; the bridge calls back into it to execute user-defined custom tools. |
| `sdk_store_callback_service.proto` | `SdkStoreCallbackService` — implemented **by the adapter** for custom agent stores. |
| `sdk_messages.proto` | Shared messages, enums, and the run-stream envelope. |
| `sdk_errors.proto` | Structured error details and the stable error-code taxonomy. |

## Getting the bridge

Prebuilt standalone archives are published for every release:

```text
https://downloads.cursor.com/sdk-bridge/<version>/<os>/<arch>/cursor-sdk-bridge-package.tar.gz
```

- `<version>` — the released SDK version (matches this repo's `vX.Y.Z` tags)
- `<os>` — `linux` | `darwin` | `win32`
- `<arch>` — `x64` | `arm64` (win32 is `x64` only)

Each archive unpacks to a `cursor-sdk-bridge/` directory containing:

- `bin/cursor-sdk-bridge` (or `bin\cursor-sdk-bridge.cmd` on Windows) — the launcher
- `manifest.json` — `bridgeVersion`, `sdkVersion`, `os`, `arch`, `entrypoint`, `protocol` (`"sdk.v1"`)
- `proto/sdk/v1/` — the exact proto contract this bridge implements
- a bundled Node.js runtime plus the npm-published `@cursor/sdk`

The bridge is also embedded in the `cursor-sdk` Python wheels on PyPI (one
wheel per platform), and `@cursor/sdk` on npm is the entry point for the
TypeScript SDK itself.

## Quickstart: spawn and handshake

1. Spawn `bin/cursor-sdk-bridge` with `CURSOR_API_KEY` set in its environment
   (create a key at [cursor.com/dashboard](https://cursor.com/dashboard)).
2. Read the bridge's **stderr** until a line starting with the literal prefix
   `cursor-sdk-bridge ready ` appears. The rest of the line is JSON:

   ```json
   {"schemaVersion":1,"transport":"tcp","protocol":"connect",
    "host":"127.0.0.1","port":49152,"url":"http://127.0.0.1:49152",
    "authTokenFile":"/tmp/cursor-sdk-bridge-abc/auth-token", "...":"..."}
   ```

3. Read the bearer token from `authTokenFile` (trim whitespace).
4. Send `Authorization: Bearer <token>` on **every** RPC. Requests without it
   are rejected with `UNAUTHENTICATED`.
5. Call `SdkBridgeControlService.Ping` to confirm the connection, then use
   `SdkAgentService` to create an agent and stream a run.

See [`docs/protocol.md`](docs/protocol.md) for the full lifecycle and
[`examples/python-adapter/`](examples/python-adapter/) for a complete working
adapter — a miniature Python SDK with `Client` / `Agent` / `Run` handles
built on this protocol.

## Documentation

- [`docs/protocol.md`](docs/protocol.md) — spawn-and-handshake lifecycle, authentication, CLI flags, distribution
- [`docs/services.md`](docs/services.md) — the role of each service, including the adapter-implemented callback services
- [`docs/streaming.md`](docs/streaming.md) — run stream semantics: envelopes, offsets, resume, keepalives
- [`docs/errors.md`](docs/errors.md) — the structured error model from `sdk_errors.proto`
- [`docs/versioning.md`](docs/versioning.md) — tag policy and the `sdk.v1` compatibility promise

---

## Agent: start here

This section is the build plan for coding agents (and humans) building a
full Cursor SDK for a new language on top of the `sdk.v1` bridge protocol —
codegen, managed bridge lifecycle, client/agent/run API design, streaming,
errors, and the adapter-side callback services.

An *adapter* spawns `cursor-sdk-bridge` and speaks the `sdk.v1` Connect
protocol to it. The end state of this guide is not a demo script but a real
SDK: a library another developer can install and use to script Cursor agents
without knowing the bridge exists. Read [`docs/protocol.md`](docs/protocol.md)
first; [`examples/python-adapter/`](examples/python-adapter/) is a working
miniature of the target shape — one module per architecture-table component,
over a hand-rolled transport that keeps the wire format visible — and
[`docs/streaming.md`](docs/streaming.md) / [`docs/errors.md`](docs/errors.md)
cover streams and failures.

### The target architecture

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

### Prerequisites and constraints

- **Pin the contract to the latest release of this repository**: the newest
  [`vX.Y.Z` tag](https://github.com/cursor/sdk-bridge/tags). Get the protos
  from `proto/sdk/v1/` at that tag (if you are working outside a checkout of
  this repo, vendor them into your project — the bridge archive for the same
  version also ships an identical `proto/sdk/v1/`). Never edit anything
  under `proto/` — it is generated.
- The target language needs (a) a protobuf runtime and (b) an HTTP/1.1
  client. A [Connect](https://connectrpc.com/docs/) client library is ideal
  but not required — every RPC is
  `POST http://<host>:<port>/sdk.v1.<Service>/<Method>` with a protobuf
  (`content-type: application/proto`) or JSON (`application/json`) body.
  **Classic gRPC will not work**: the bridge serves HTTP/1.1 only.
- Running a real turn needs a `CURSOR_API_KEY`
  ([cursor.com/dashboard](https://cursor.com/dashboard)).

Work through the milestones below **in order**, and keep a runnable
demo/test at every milestone — each one builds on a verified previous layer.

### Milestone 1 — Codegen

Copy `examples/python-adapter/buf.gen.yaml` as a template: point `inputs` at
your copy of the protos (`directory: ../../proto` when working inside this
repository) and swap the plugins for the target language's protobuf + Connect
plugins. For compiled languages, use buf's `managed` mode to override
language package options — the published protos carry Cursor-internal values for
`go_package`, `java_package`, and friends. Only `sdk/v1/*.proto` and Google
well-known types are involved; no other dependencies. Commit the
`buf.gen.yaml`, gitignore the `gen/` output.

If the language has no Connect plugin, generate plain protobuf messages and
hand-write the tiny HTTP layer (unary = one POST; server streams = the
Connect streaming envelope: 1-byte flags + 4-byte big-endian length frames,
end-of-stream flag `0x02` carrying a JSON EndStreamResponse with any error).
`examples/python-adapter/cursor_adapter/_transport.py` does exactly this in
~100 lines.

### Milestone 2 — Bridge manager

- Locate the bridge: an env override such as `CURSOR_SDK_BRIDGE_BIN` first,
  then your package's bundled/downloaded archive
  (`https://downloads.cursor.com/sdk-bridge/<version>/<os>/<arch>/cursor-sdk-bridge-package.tar.gz`,
  os: `linux|darwin|win32`, arch: `x64|arm64`; launcher at
  `cursor-sdk-bridge/bin/cursor-sdk-bridge`, `.cmd` on Windows). Use the
  `<version>` matching the tag you pinned.
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

### Milestone 3 — Transport, auth, and errors

- Send `Authorization: Bearer <token>` on **every** request — unary *and*
  streaming (a common bug: interceptor APIs often cover only unary).
  Missing/wrong token ⇒ `UNAUTHENTICATED`.
- Verify with `SdkBridgeControlService.Ping`, then `GetVersion` (expect
  `protocol_version == "sdk.v1"`; capabilities gate optional features).
- Build the error layer now, not last: decode `sdk.v1.SdkErrorDetails` from
  failed RPCs ([`docs/errors.md`](docs/errors.md) has the taxonomy and wire
  encoding) and map `sdk_error_code` + Connect code onto your language's
  exception/error hierarchy. Expose the full `request_id`, `retry_after`,
  and `rate_limit`. Parse protobuf JSON with unknown-field tolerance
  everywhere.

### Milestone 4 — First turn: `Agent.send` → `Run`

1. `SdkAgentService.CreateAgent` with `options.local.cwd = ["<workspace>"]`
   and an explicit `options.model` — local agents require one; discover IDs
   via `SdkCursorService.ListModels` (catalog calls **require** a per-call
   `api_key`; there is no env fallback).
2. `SdkAgentService.Send` with the `agent_id` and a `UserMessage{text}`;
   wrap the server stream in your `Run` handle per
   [`docs/streaming.md`](docs/streaming.md):
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
     [`docs/streaming.md`](docs/streaming.md)), and `wait()` falls back to
     `WaitLiveRun`.
3. Layer the conveniences on the raw event stream: assistant-text iterator,
   blocking `wait()`, terminal `text()`, `cancel()`.

### Milestone 5 — Management surface and catalog

Fill out the rest of `SdkAgentService` on `Client`/`Agent`: `ResumeAgent`,
`GetAgent`/`ListAgents` (pagination cursors), `ArchiveAgent`/`Unarchive`/
`Delete`/`Close`, `ListRuns`/`GetRun`/`GetRunConversation`,
`ListAgentMessages`, artifacts (`ListArtifacts` + chunked
`DownloadArtifact`), `GetUsage` (cloud only), and the `Cursor` catalog
(`Me`, `ListModels`, `ListRepositories`). These are mechanical once
milestones 1–4 work.

### Milestone 6 — Callback services (custom tools / stores)

These invert direction: the SDK runs a loopback Connect **server** and the
bridge authenticates to it with a bearer token the SDK chooses. Validate that
token on every callback, exactly as the bridge validates yours. Gotchas that
cost real debugging time (details in [`docs/services.md`](docs/services.md)):
callback POSTs may use chunked transfer-encoding (decode it — minimal HTTP
servers often don't); store outputs must be the bare record, not the wrapped
input envelope; tool results are `Struct`s, so scalar returns need wrapping
in an object.

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

### Verification checklist

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

## License

[MIT](LICENSE)
