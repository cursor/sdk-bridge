# Python adapter example

A minimal, complete `sdk.v1` adapter in Python. It:

1. downloads (via `fetch-bridge.sh`) and spawns the standalone bridge;
2. performs the handshake — waits for the `cursor-sdk-bridge ready ` stderr
   line, reads the bearer token from `authTokenFile`;
3. authenticates every RPC with `Authorization: Bearer <token>`;
4. verifies the connection with `Ping` / `GetVersion`;
5. creates a local agent, sends one message, and streams the run to stdout;
6. shuts the bridge down gracefully.

Unlike the Go example, the RPC layer is hand-rolled: plain protobuf codegen
plus a small Connect-over-HTTP/1.1 client on the standard library (`urllib`).
The bridge's protocol is simple enough — unary RPCs are single POSTs, server
streams are enveloped frames — that this keeps the example to one runtime
dependency (`protobuf`) and makes the wire protocol visible in the code. A
[connect-python](https://connectrpc.com/) client works just as well if you
prefer generated stubs. Either way, classic gRPC clients will not work — the
bridge is HTTP/1.1 only (see [`docs/protocol.md`](../../docs/protocol.md)).

## Prerequisites

- Python 3.10+
- [buf](https://buf.build/docs/installation) v1.32+ (codegen uses buf's
  remote plugins, so no local protoc plugins are needed)
- A Cursor API key from [cursor.com/dashboard](https://cursor.com/dashboard)

## 1. Generate the messages

From this directory (requires `proto/` at the repo root — present after the
first release sync; at a tag it matches that release exactly):

```bash
buf generate
```

This reads `../../proto` and writes protobuf modules and type stubs to
`gen/` (gitignored). If buf's remote plugins are unreachable from your
network, the equivalent local command is:

```bash
pip install grpcio-tools
python -m grpc_tools.protoc -I ../../proto --python_out=gen --pyi_out=gen \
  ../../proto/sdk/v1/*.proto
```

## 2. Install the runtime dependency

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## 3. Get the bridge

```bash
./fetch-bridge.sh <version>   # e.g. ./fetch-bridge.sh 1.0.26
```

This downloads the archive for your OS/arch from
`https://downloads.cursor.com/sdk-bridge/<version>/<os>/<arch>/cursor-sdk-bridge-package.tar.gz`
and unpacks it to `./cursor-sdk-bridge/`. Alternatively, point the example at
any existing bridge launcher with `CURSOR_SDK_BRIDGE_BIN`.

## 4. Run one agent turn

```bash
export CURSOR_API_KEY=key_...
python main.py --workspace /path/to/some/project --prompt "Summarize this repository."
```

Expected output (abridged):

```text
bridge ready: url=http://127.0.0.1:41317 serverVersion=1.0.0
ping ok; bridge 1.0.0 protocol sdk.v1
agent created: <agent-id> (model <model-id>)
[system] run started: <run-id>
[status RUNNING]
[assistant] This repository contains ...
[tool_call running] read_file
[tool_call completed] read_file
run finished: status=FINISHED
bridge stopped
```

## Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--bridge` | `$CURSOR_SDK_BRIDGE_BIN`, else `./cursor-sdk-bridge/bin/cursor-sdk-bridge` | Bridge launcher to spawn. |
| `--workspace` | current directory | Workspace the local agent works in. |
| `--prompt` | `Say hello and name one file in this workspace.` | The user message to send. |
| `--model` | first model from `SdkCursorService.ListModels` | Model ID, e.g. `composer-2`. Local agents require an explicit model. |
