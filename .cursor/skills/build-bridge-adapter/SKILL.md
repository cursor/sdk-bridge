---
name: build-bridge-adapter
description: Build a Cursor SDK bridge adapter in a new language from the sdk.v1 protos — codegen, spawning the bridge, the ready-line handshake, bearer auth, running a first agent turn, and the adapter-side callback services. Use when asked to build, port, or debug an adapter/SDK/client for the Cursor SDK bridge in any language.
---

# Build a bridge adapter in a new language

An *adapter* spawns `cursor-sdk-bridge` and speaks the `sdk.v1` Connect
protocol to it. This skill walks through building one from scratch. Read
`docs/protocol.md` first; use `examples/go-adapter/` as the reference
implementation, and keep `docs/streaming.md` / `docs/errors.md` open while
implementing streams and error handling.

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

## Step 1 — Codegen

Copy `examples/go-adapter/buf.gen.yaml` as a template: keep
`inputs: [directory: ../../proto]`, swap the plugins for the target
language's protobuf + Connect plugins, and use `managed` mode to override
language package options (the synced protos carry Cursor-internal `go_package`
etc.). Only `sdk/v1/*.proto` and Google well-known types are involved; no
other dependencies. Commit the `buf.gen.yaml`, gitignore the `gen/` output.

If the language has no Connect plugin, generate plain protobuf messages and
hand-write the tiny HTTP layer (unary = one POST; server streams = the
Connect streaming envelope: 1-byte flags + 4-byte big-endian length frames,
end-of-stream flag `0x02`).

## Step 2 — Spawn the bridge

- Get a bridge: download
  `https://downloads.cursor.com/sdk-bridge/<version>/<os>/<arch>/cursor-sdk-bridge-package.tar.gz`
  (os: `linux|darwin|win32`, arch: `x64|arm64`) and unpack; the launcher is
  `cursor-sdk-bridge/bin/cursor-sdk-bridge` (`.cmd` on Windows). For dev
  machines also honor a `CURSOR_SDK_BRIDGE_BIN` override.
- Spawn it with `CURSOR_API_KEY` in the environment, plus
  `--workspace <dir>` for local agents. Set
  `CURSOR_SDK_CLIENT_LANGUAGE=<language>` so traffic is attributable.
- Capture **stderr** (keep draining it forever — a full pipe blocks the
  bridge).

## Step 3 — The handshake

Scan stderr lines for the literal prefix `cursor-sdk-bridge ready ` (trailing
space). Parse the remainder as JSON and validate `schemaVersion == 1`,
`transport == "tcp"`, `protocol == "connect"`; reject otherwise, ignore
unknown fields. Apply a ~30s startup timeout; if the process exits first,
surface the captured stderr. Then read the bearer token from the
`authTokenFile` path and trim whitespace. Never log the raw discovery line
(older bridges inline the token).

## Step 4 — Auth and first RPCs

Send `Authorization: Bearer <token>` on **every** request — unary *and*
streaming (a common bug: language interceptor APIs often cover only unary).
Missing/wrong token ⇒ `UNAUTHENTICATED`.

Verify with `SdkBridgeControlService.Ping`, then `GetVersion` (expect
`protocol_version == "sdk.v1"`; capabilities gate optional features).

## Step 5 — First agent turn

1. `SdkAgentService.CreateAgent` with `options.local.cwd = ["<workspace>"]`
   and an explicit `options.model` — local agents require one; discover IDs
   via `SdkCursorService.ListModels`.
2. `SdkAgentService.Send` with the `agent_id` and a `UserMessage{text}`.
3. Consume the `RunStreamMessage` stream per `docs/streaming.md`:
   - dispatch on the `envelope` oneof; **ignore** messages with no case set
     (keepalives) and unknown cases;
   - `sdk_message`: dispatch on `type` (`system`, `assistant`, `tool_call`,
     ...); payloads are JSON objects (`google.protobuf.Struct`);
   - `result` then `done` end the run; a dropped stream does **not** cancel
     the run — resume with `ObserveRun` + the last seen `offset`.
4. Shut down: `SdkBridgeControlService.Shutdown` (or SIGTERM), wait ~5s,
   then kill.

## Step 6 — Error handling

Decode the `sdk.v1.SdkErrorDetails` error detail from failed RPCs and expose
`sdk_error_code`, the full `request_id`, `retry_after`, and `rate_limit` to
callers (`docs/errors.md` has the taxonomy). Parse protobuf JSON with
unknown-field tolerance everywhere.

## Step 7 — Callback services (optional, for custom tools / stores)

These invert direction: the adapter runs a loopback Connect **server** and the
bridge authenticates to it with a bearer token the adapter chooses.

- **Custom tools** — implement `SdkCustomToolCallbackService.CallCustomTool`
  (execute the named tool with the Struct args, return a Struct result).
  Declare tool metadata in `LocalAgentOptions.custom_tools` on CreateAgent;
  register the server via `--tool-callback-url`/`--tool-callback-auth-token`
  or `SdkBridgeControlService.SetToolCallback`.
- **Custom stores** — implement `SdkStoreCallbackService.CallStore`
  (substores `agents|runs|runEvents|checkpoints`; methods
  `get|create|update|delete|list|append`; checkpoint blobs are base64).
  Launch the bridge with `LocalAgentStoreConfig{type:"custom"}` (e.g.
  `--local-store '{"type":"custom"}'`) plus
  `--store-callback-url`/`--store-callback-auth-token` (launch-time only).

Validate the bridge's bearer token on every callback, exactly as the bridge
validates yours.

## Verification checklist

- [ ] Handshake: ready line parsed, token read from file, `Ping` succeeds.
- [ ] A missing `Authorization` header fails with `UNAUTHENTICATED`.
- [ ] One full turn: `CreateAgent` → `Send` → assistant output → `result` →
      `done`, against a real `CURSOR_API_KEY`.
- [ ] Keepalive frames (empty envelope) are ignored; a >15s tool pause does
      not break the stream.
- [ ] Bridge exits cleanly on `Shutdown`; adapter kills it on timeout.
- [ ] Failed RPCs surface `sdk_error_code` and full `request_id`.
