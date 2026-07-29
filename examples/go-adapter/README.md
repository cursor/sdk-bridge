# Go adapter example

A minimal, complete `sdk.v1` adapter in Go. It:

1. downloads (via `fetch-bridge.sh`) and spawns the standalone bridge;
2. performs the handshake — waits for the `cursor-sdk-bridge ready ` stderr
   line, reads the bearer token from `authTokenFile`;
3. authenticates every RPC with `Authorization: Bearer <token>`;
4. verifies the connection with `Ping` / `GetVersion`;
5. creates a local agent, sends one message, and streams the run to stdout;
6. shuts the bridge down gracefully.

It uses [connect-go](https://connectrpc.com/docs/go/getting-started); the
bridge serves the Connect protocol over HTTP/1.1 (classic gRPC clients will
not work — see [`docs/protocol.md`](../../docs/protocol.md)).

## Prerequisites

- Go 1.22+
- [buf](https://buf.build/docs/installation) v1.32+ (for codegen)
- The protoc plugins used by `buf.gen.yaml`:

  ```bash
  go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
  go install connectrpc.com/connect/cmd/protoc-gen-connect-go@latest
  ```

- A Cursor API key from [cursor.com/dashboard](https://cursor.com/dashboard)

## 1. Generate the client

From this directory (requires `proto/` at the repo root — present after the
first release sync; at a tag it matches that release exactly):

```bash
buf generate
```

This reads `../../proto` and writes Go message types and Connect clients to
`gen/` (gitignored).

## 2. Get the bridge

```bash
./fetch-bridge.sh <version>   # e.g. ./fetch-bridge.sh 1.0.26
```

This downloads the archive for your OS/arch from
`https://downloads.cursor.com/sdk-bridge/<version>/<os>/<arch>/cursor-sdk-bridge-package.tar.gz`
and unpacks it to `./cursor-sdk-bridge/`. Alternatively, point the example at
any existing bridge launcher with `CURSOR_SDK_BRIDGE_BIN`.

## 3. Run one agent turn

```bash
export CURSOR_API_KEY=key_...
go run . -workspace /path/to/some/project -prompt "Summarize this repository."
```

Expected output (abridged):

```text
bridge ready: url=http://127.0.0.1:41317 serverVersion=1.0.0
ping ok; bridge 1.0.0 protocol sdk.v1
agent created: <agent-id> (model <model-id>)
[system] run started: <run-id>
[assistant] This repository contains ...
[tool_call running] read_file
[tool_call completed] read_file
run finished: status=FINISHED
bridge stopped
```

## Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `-bridge` | `$CURSOR_SDK_BRIDGE_BIN`, else `./cursor-sdk-bridge/bin/cursor-sdk-bridge` | Bridge launcher to spawn. |
| `-workspace` | current directory | Workspace the local agent works in. |
| `-prompt` | `Say hello and name one file in this workspace.` | The user message to send. |
| `-model` | first model from `SdkCursorService.ListModels` | Model ID, e.g. `composer-2`. Local agents require an explicit model. |
