# The bridge protocol: lifecycle and handshake

This guide describes how an adapter obtains, spawns, and talks to
`cursor-sdk-bridge`. The wire contract is defined by the protos in
[`proto/sdk/v1/`](../proto/sdk/v1/); this document covers everything around
them: process lifecycle, discovery, and authentication.

## What the bridge is

The bridge is a local Connect server that wraps Cursor's TypeScript SDK
(`@cursor/sdk`). It runs as a child process of your adapter, binds a loopback
TCP port, and exposes the `sdk.v1` services. Your adapter owns the process:
it spawns the bridge, performs the handshake, issues RPCs, and shuts it down.

Two authentication domains are involved, and they are independent:

1. **Bridge auth** — a per-process bearer token, generated fresh on every
   launch, that protects the local RPC endpoint. Your adapter learns it during
   the handshake and must send it on every RPC.
2. **Cursor auth** — the `CURSOR_API_KEY` environment variable, which the
   bridge process uses to reach Cursor's API. Agent operations default to it;
   some request messages accept a per-call `api_key` override, and
   `SdkCursorService` catalog calls require one (see
   [`services.md`](services.md)).

## Obtaining the bridge

Prebuilt standalone archives are attached to every
[release of this repository](https://github.com/cursor/sdk-bridge/releases).
Download `cursor-sdk-bridge-standalone-<os>-<arch>.tar.gz` for your platform
from the [latest release](https://github.com/cursor/sdk-bridge/releases/latest):

- `<os>` — `linux` | `darwin` | `win32`
- `<arch>` — `x64` | `arm64` (win32 is `x64` only)

Release tags `vX.Y.Z` match the released SDK version — to pin one, download
from that tag's release page instead of `latest`. Each release also attaches
a `SHA256SUMS.txt` covering its archives.

The archive unpacks in place (no top-level directory):

```text
bin/cursor-sdk-bridge      # self-contained executable (cursor-sdk-bridge.exe on Windows)
proto/sdk/v1/              # the exact proto contract this bridge implements
manifest.json
```

`manifest.json` describes the archive:

```json
{
  "bridgeVersion": "1.0.0",
  "sdkVersion": "1.0.26",
  "os": "linux",
  "arch": "x64",
  "entrypoint": "bin/cursor-sdk-bridge",
  "protocol": "sdk.v1",
  "distribution": "standalone",
  "runtime": "bun-1.3.9"
}
```

Adapters should launch `entrypoint` and may assert `protocol == "sdk.v1"`
before doing so. The bridge is also embedded per-platform in the `cursor-sdk`
Python wheels on PyPI, so a machine with the Python SDK installed already has
a copy.

## Spawning and the ready line

Spawn the executable with `CURSOR_API_KEY` in its environment and capture
**stderr**. The bridge prints exactly one discovery line to stderr once it is
listening — a literal prefix followed by a single JSON object:

```text
cursor-sdk-bridge ready {"schemaVersion":1,"serverVersion":"1.0.0","pid":12345,"transport":"tcp","protocol":"connect","host":"127.0.0.1","port":49152,"url":"http://127.0.0.1:49152","authTokenFile":"/tmp/cursor-sdk-bridge-Xxxxxx/auth-token","workspaceRef":"/home/me/project","stateRoot":"/home/me/.cursor/sdk-agent-store/abc123"}
```

Parsing rules (all verified against the reference adapters):

- Scan stderr line by line for the prefix `cursor-sdk-bridge ready ` (note the
  trailing space). Lines without the prefix are ordinary diagnostics — forward
  them to your logs but keep scanning.
- Everything after the prefix is JSON. Fields:

  | Field | Meaning |
  | --- | --- |
  | `schemaVersion` | Discovery payload schema. Reject anything other than `1`. |
  | `serverVersion` | Bridge build version. |
  | `pid` | Bridge process ID. |
  | `transport` | Always `"tcp"`. Reject other values. |
  | `protocol` | Always `"connect"`. Reject other values. |
  | `host`, `port`, `url` | Where to connect. Prefer `url`; fall back to `host` + `port` (bracket IPv6 hosts). |
  | `authTokenFile` | Path to the bearer-token file (see below). |
  | `workspaceRef` | Resolved workspace directory the bridge was launched for. |
  | `stateRoot` | Directory for bridge-owned durable agent state. |
  | `maxConcurrentAgents`, `maxMessageBytes` | Advertised limits, present when configured. |

- Apply a startup timeout (the reference adapters use 30 seconds). If the
  process exits before emitting the line, surface the captured stderr as the
  error message.
- Treat unknown extra JSON fields as forward-compatible additions and ignore
  them.

## The auth token file and bearer auth

`authTokenFile` points to a file (mode `0600`, in a fresh temp directory)
containing a random base64url token. Read it, trim surrounding whitespace, and
send it as a header on **every** RPC:

```text
Authorization: Bearer <token>
```

Connections without a valid token are rejected with the Connect/gRPC code
`UNAUTHENTICATED`. The token is per-process: a new one is generated each
launch, and it is never printed to stderr by current bridges. (Older bridges
may include an inline `authToken` field in the discovery JSON; if present,
prefer it, but never log the discovery line verbatim.)

## Speaking to the bridge

The bridge serves the [Connect protocol](https://connectrpc.com/docs/protocol)
and gRPC-Web over **HTTP/1.1**. Classic gRPC requires HTTP/2 and will not
work; use a Connect client (available for Go, Python via `connect-python`,
Kotlin, Swift, Node, ...) or plain HTTP POSTs — every RPC is
`POST http://<host>:<port>/sdk.v1.<Service>/<Method>` with a protobuf or JSON
body.

A good first RPC is `SdkBridgeControlService.Ping`, followed by `GetVersion`,
which reports the bridge build version, the protocol version (`"sdk.v1"`), and
a list of capability strings for feature negotiation.

The bridge binds `127.0.0.1` by default and refuses to bind non-loopback hosts
unless explicitly configured otherwise. Treat the endpoint as local-only.

Optionally set `CURSOR_SDK_CLIENT_LANGUAGE=<language>` in the bridge's
environment so Cursor can attribute traffic to your adapter's language (the
official adapters set `go` / `python`).

## CLI flags and environment variables

| Flag | Env var | Meaning |
| --- | --- | --- |
| `--host <host>` | `CURSOR_SDK_BRIDGE_HOST` | Host to bind (default `127.0.0.1`). |
| `--port <port>` | `CURSOR_SDK_BRIDGE_PORT` | Port to bind (default `0` = ephemeral; read the actual port from the ready line). |
| `--workspace <path>` | `CURSOR_SDK_BRIDGE_WORKSPACE` | Workspace root; the default `cwd` for local agents and store discovery. |
| `--state-root <path>` | `CURSOR_SDK_BRIDGE_STATE_ROOT` | Root directory for durable local agent state (defaults to a per-workspace directory under `~/.cursor/sdk-agent-store/`). |
| `--local-store <json>` | `CURSOR_SDK_LOCAL_STORE` | Default local agent store config, as `LocalAgentStoreConfig` JSON (`{"type":"sqlite"}`, `{"type":"jsonl","rootDir":...}`, or `{"type":"custom"}`). |
| `--store-callback-url <url>` | `CURSOR_SDK_STORE_CALLBACK_URL` | Base URL of the adapter's `SdkStoreCallbackService` server (required for `"custom"` stores; must be set together with the token). |
| `--store-callback-auth-token <token>` | `CURSOR_SDK_STORE_CALLBACK_AUTH_TOKEN` | Bearer token the bridge presents on store callbacks. |
| `--tool-callback-url <url>` | `CURSOR_SDK_TOOL_CALLBACK_URL` | Base URL of the adapter's `SdkCustomToolCallbackService` server (must be set together with the token). |
| `--tool-callback-auth-token <token>` | `CURSOR_SDK_TOOL_CALLBACK_AUTH_TOKEN` | Bearer token the bridge presents on tool callbacks. |
| `--max-concurrent-agents <count>` | — | Advertised agent concurrency limit. |
| `--max-message-bytes <bytes>` | — | Advertised max message size. |
| `--help`, `-h` | — | Print usage. |

Callback URL/token pairs must be provided together; supplying only one is a
startup error. Tool callbacks can also be registered after startup via
`SdkBridgeControlService.SetToolCallback` (same-host/loopback only).

## Shutdown

Prefer a graceful stop, then escalate:

1. Call `SdkBridgeControlService.Shutdown` (`grace_seconds` bounds how long
   in-flight RPCs may drain; `0` means immediate) and wait for the process to
   exit, **or** send `SIGINT`/`SIGTERM` — the bridge handles both.
2. If the process does not exit within your timeout (the reference adapters
   use ~5 seconds), kill it.

Shutdown releases local resources only; durable cloud agent state is not
affected.

## Suggested handshake sequence

```text
adapter                                bridge
  │ spawn bin/cursor-sdk-bridge          │
  │   (CURSOR_API_KEY in env)            │
  │ ────────────────────────────────────►│
  │                                      │ binds 127.0.0.1:<port>
  │        stderr: "cursor-sdk-bridge ready {...}"
  │ ◄────────────────────────────────────│
  │ read authTokenFile, trim             │
  │                                      │
  │ POST /sdk.v1.SdkBridgeControlService/Ping
  │   Authorization: Bearer <token>      │
  │ ────────────────────────────────────►│
  │ ◄──────────────────── PingResponse ──│
  │                                      │
  │ CreateAgent → Send (stream) → ...    │
  │                                      │
  │ Shutdown / SIGTERM ─────────────────►│
  │                          process exits
```

Next: [`services.md`](services.md) for what each service does,
[`streaming.md`](streaming.md) for run streams, and
[`errors.md`](errors.md) for the failure model.
