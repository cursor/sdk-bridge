# Services

`sdk.v1` defines five services. Three are served **by the bridge**; two are
callback services served **by your adapter** that the bridge calls into.

```text
served by the bridge                     served by the adapter
────────────────────                     ─────────────────────
SdkAgentService                          SdkCustomToolCallbackService
SdkCursorService                         SdkStoreCallbackService
SdkBridgeControlService
```

## Bridge-served services

### `SdkAgentService` (`sdk_agent_service.proto`)

The core surface: agent lifecycle, message sending, run streaming, and
artifacts. Agents come in two runtimes, selected by `AgentOptions`:

- **Local agents** (`AgentOptions.local`) run on the machine hosting the
  bridge, against one or more working directories (`local.cwd`). They require
  an explicit `AgentOptions.model` (discover IDs via
  `SdkCursorService.ListModels`). Their durable state lives in the bridge's
  local agent store (SQLite by default; see the store callback service for
  fully adapter-owned storage).
- **Cloud agents** (`AgentOptions.cloud`) run in Cursor's cloud (or a
  self-hosted worker pool / machine) against git repositories.

Highlights:

| RPC | Notes |
| --- | --- |
| `CreateAgent` / `ResumeAgent` | Create a new agent or re-attach to an existing one with updated options. `CreateAgent` accepts an optional `idempotency_key` for safe retries on cloud agents. |
| `Send` | Send a `UserMessage` and stream `RunStreamMessage` events until the run completes. This is the main streaming RPC — see [`streaming.md`](streaming.md). |
| `ObserveRun` | Subscribe to a run's durable events, optionally resuming after a prior stream `offset`. |
| `WaitLiveRun` / `GetRun` / `ListRuns` / `GetRunConversation` | Blocking wait, point-in-time snapshots, listing, and the raw conversation JSON. |
| `CancelRun` | Request cancellation of an in-flight run. |
| `GetAgent` / `ListAgents` / `ArchiveAgent` / `UnarchiveAgent` / `DeleteAgent` / `CloseAgent` | Agent management. `CloseAgent` releases local resources only; `DeleteAgent` removes durable data. |
| `ListAgentMessages` | Messages recorded for an agent. |
| `ListArtifacts` / `DownloadArtifact` | Cloud agent artifacts; downloads stream `DownloadArtifactChunk` bytes. |
| `GetUsage` | Billed token usage and cost. Cloud agents only. |

### `SdkCursorService` (`sdk_cursor_service.proto`)

Client-level operations against Cursor's API that need no agent runtime:

- `Me` — the authenticated account identity for the API key.
- `ListModels` — models available to the account, including parameter and
  variant metadata.
- `ListRepositories` — repositories usable with cloud agents.

Each request carries `CursorRequestOptions.api_key`, and for these catalog
RPCs it is **required**: current bridges fail closed with `UNAUTHENTICATED`
(`"API key is required for cloud catalog calls."`) rather than falling back
to the bridge's environment.

Agent operations also accept an explicit key (`AgentOptions.api_key`), and
adapters should **always set it** rather than relying on the bridge's
`CURSOR_API_KEY` env var: not every operation falls back to the env var on
every bridge build — on some, runs on an agent created without an explicit
`api_key` fail with `Invalid User API Key`. Setting the option works
everywhere.

### `SdkBridgeControlService` (`sdk_bridge_control_service.proto`)

Manages the bridge process itself:

- `Ping` — liveness; the natural first RPC after the handshake.
- `GetVersion` — `bridge_version`, `protocol_version` (`"sdk.v1"`), and a list
  of capability strings (for example `agent.create`, `run.observe`,
  `artifacts.chunked`) for feature negotiation. Treat unknown capability
  strings as forward-compatible additions.
- `Shutdown` — graceful shutdown with a `grace_seconds` drain window.
- `SetToolCallback` — register (or clear, with an empty URL) the adapter's
  custom-tool callback endpoint after startup. Same-host/loopback only;
  equivalent to launching with `--tool-callback-url`/`--tool-callback-auth-token`.

## Adapter-served callback services

Both callback services invert the connection direction: your adapter runs a
small Connect server on loopback, tells the bridge its URL plus a bearer token
you choose, and the bridge authenticates to *you* with that token on every
callback. Validate it exactly like the bridge validates yours.

Implementation note for hand-rolled servers: the bridge's callback requests
are ordinary Connect unary POSTs but may arrive with
`Transfer-Encoding: chunked` and no `Content-Length`. Minimal HTTP server
libraries often do not decode chunked request bodies for you — handle both
framings or callbacks will appear empty.

### `SdkCustomToolCallbackService` (`sdk_custom_tool_callback_service.proto`)

Custom tools let agent code call functions defined in your adapter's language.
The split is:

- **Metadata travels with agent options.** Declare tools in
  `LocalAgentOptions.custom_tools` (name → `CustomToolDefinition` with a
  description and a JSON Schema `input_schema`) on `CreateAgent`/`ResumeAgent`.
- **Execution round-trips to the adapter.** When the agent invokes a tool, the
  bridge calls `CallCustomTool` on your server with the `tool_name`, the
  arguments as a JSON object (`google.protobuf.Struct`), an optional
  `tool_call_id` for correlating with stream events, and the owning
  `agent_id`. Your response's `result` is a `Struct` and therefore must be a
  JSON **object** — wrap scalar results (for example `{"value": "..."}`) or
  use a content envelope the SDK recognizes; a bare string cannot be encoded.

Register the endpoint at launch (`--tool-callback-url` +
`--tool-callback-auth-token`) or at runtime via
`SdkBridgeControlService.SetToolCallback`. Custom tools are a **local agent**
feature.

### `SdkStoreCallbackService` (`sdk_store_callback_service.proto`)

By default the bridge persists local agent state itself (`"sqlite"`, or
`"jsonl"` with a `root_dir`). Setting `LocalAgentStoreConfig.type` to
`"custom"` hands the entire store to your adapter: the bridge forwards every
store operation over a single generic RPC.

`CallStore` requests carry:

- `substore` — `"agents"`, `"runs"`, `"runEvents"`, or `"checkpoints"`,
  mirroring the local agent store topology;
- `method` — `"get"`, `"create"`, `"update"`, `"delete"`, `"list"`, or
  `"append"` (`runEvents` only);
- `input` — the operation input as a JSON object. Checkpoint blob bytes are
  base64-encoded strings.

Return the operation output in `output`, or leave it unset for a null result
(a `get` miss or a `delete`). The store callback endpoint can only be
configured at launch (`--store-callback-url` +
`--store-callback-auth-token`), since agents may load state before any RPC
arrives.

The `input`/`output` objects mirror the SDK's store interface, and the
structural rules matter more than exact fields (which follow the SDK's
document types and may gain fields over time):

- `create`/`update` inputs wrap the record under a singular key (for example
  `{"agent": {...}}`); `get` inputs carry bare id fields (for example
  `{"agentId": ...}`).
- Outputs must be the **bare record object** — echoing the wrapped input
  envelope back causes opaque internal errors in the bridge.
- `runEvents.append` input is `{"runId", "eventType", "payload"}`.
- `checkpoints` blobs are base64 strings: `create`/`update` input is
  `{"agentId", "blobId", "data"}`; `get` returns `{"found": bool, "data":
  <base64>}`.

When building a store, log the live traffic from a real agent turn first —
one `CreateAgent` + `Send` exercises most substores and methods.

## Import graph

`sdk_messages.proto` holds all shared types and is imported by the agent and
cursor services; `sdk_errors.proto` stands alone (error details arrive inside
Connect/gRPC error metadata, not as response fields). The only external
imports are Google well-known types (`struct`, `timestamp`, `duration`), so
codegen needs nothing beyond `proto/sdk/v1` and the standard protobuf runtime.
