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
this repository. (The non-TypeScript SDKs invert this: the `cursor-sdk` Python
package embeds and spawns the bridge.) An *adapter* is anything that spawns
the bridge and speaks `sdk.v1` to it: an SDK for a new language, a service
integration, or a one-off script.

```text
┌────────────────┐  spawn + Connect RPCs   ┌────────────────────┐   HTTPS    ┌─────────────┐
│  your adapter   │ ──────────────────────► │  cursor-sdk-bridge │ ─────────► │ Cursor API  │
│  (any language) │ ◄────────────────────── │  (local process)   │            │             │
└────────────────┘   callback RPCs (tools,  └────────────────────┘            └─────────────┘
                     custom stores)
```

## Repository layout

| Path | Contents |
| --- | --- |
| `proto/sdk/v1/` | The `sdk.v1` protobuf contract. **Generated — do not edit.** Synced automatically from Cursor's internal repository on every SDK release. |
| `proto/manifest.json` | Sync metadata: `protocol` (`"sdk.v1"`), `sdkVersion`, `sourceCommit`. |
| `docs/` | The protocol guide: lifecycle, services, streaming, errors, versioning. |
| `examples/` | Minimal adapters in other languages, each with its own `buf.gen.yaml`. |
| `.cursor/skills/` | An agent skill that walks through building a new-language adapter. |

> **Note:** `proto/` is owned by Cursor's release automation. It is deleted and
> rewritten on every sync, and every release pushes an annotated tag `vX.Y.Z`
> matching the released `@cursor/sdk` npm / `cursor-sdk` PyPI version. Pull
> requests must never touch `proto/`. If the directory is missing, the first
> sync has not run yet.

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

See [`docs/protocol.md`](docs/protocol.md) for the full lifecycle, and
[`examples/go-adapter/`](examples/go-adapter/) (connect-go) or
[`examples/python-adapter/`](examples/python-adapter/) (stdlib-only,
hand-rolled Connect client) for complete working adapters.

## Documentation

- [`docs/protocol.md`](docs/protocol.md) — spawn-and-handshake lifecycle, authentication, CLI flags, distribution
- [`docs/services.md`](docs/services.md) — the role of each service, including the adapter-implemented callback services
- [`docs/streaming.md`](docs/streaming.md) — run stream semantics: envelopes, offsets, resume, keepalives
- [`docs/errors.md`](docs/errors.md) — the structured error model from `sdk_errors.proto`
- [`docs/versioning.md`](docs/versioning.md) — sync automation, tag policy, and the `sdk.v1` compatibility promise

## Building an adapter

Generate code from `proto/sdk/v1` with [buf](https://buf.build) (each example
ships a `buf.gen.yaml`), then follow the lifecycle in the docs. If you are
using a Cursor agent to build one, point it at
[`.cursor/skills/build-bridge-adapter/SKILL.md`](.cursor/skills/build-bridge-adapter/SKILL.md).

## License

[MIT](LICENSE)
