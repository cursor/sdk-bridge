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
to the bridge's environment. Agent operations, by contrast, default to the
bridge's `CURSOR_API_KEY` when no per-call key is supplied.

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
  `agent_id`. Your response's `result` Struct is returned to the agent —
  a plain string value, a structured object, or a content envelope the SDK
  recognizes.

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

## Import graph

`sdk_messages.proto` holds all shared types and is imported by the agent and
cursor services; `sdk_errors.proto` stands alone (error details arrive inside
Connect/gRPC error metadata, not as response fields). The only external
imports are Google well-known types (`struct`, `timestamp`, `duration`), so
codegen needs nothing beyond `proto/sdk/v1` and the standard protobuf runtime.
