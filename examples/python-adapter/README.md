# Python adapter example

A miniature Cursor SDK for Python, built on the `sdk.v1` bridge protocol —
the architecture that the
[`build-bridge-adapter` skill](../../.agents/skills/build-bridge-adapter/SKILL.md)
describes, in a form another developer could `import` and use without knowing
the bridge exists:

```python
from cursor_adapter import Client

with Client() as client:                      # spawns the bridge lazily
    agent = client.agents.create(model="composer-2", cwd="/repo")
    run = agent.send("Summarize this repository.")
    for text in run.iter_text():
        print(text)
    result = run.wait()
```

or, for the simplest case:

```python
from cursor_adapter import prompt

print(prompt("Summarize this repository.", cwd="/repo"))
```

## Package layout

Each module is one component from the skill's architecture table:

| Module | Component |
| --- | --- |
| `cursor_adapter/_bridge.py` | **Bridge manager** — locate (`CURSOR_SDK_BRIDGE_BIN` → `./cursor-sdk-bridge/`), spawn, ready-line handshake, token read, shutdown escalation, `atexit` leak guard. |
| `cursor_adapter/_transport.py` | **Transport** — hand-rolled Connect-over-HTTP/1.1 on `urllib`: unary POSTs, server-stream envelope framing, bearer auth on every request. |
| `cursor_adapter/_client.py` | **`Client`** (lazy bridge, `ping`/`version`, endpoint attach), the `agents` constructors, and the **`Cursor` catalog** (`me`, `models`, `repositories`). |
| `cursor_adapter/_agent.py` | **`Agent` handle** — `send() -> Run`, `runs()`, `close`/`archive`/`delete`, context manager. |
| `cursor_adapter/_run.py` | **`Run` handle** — event iteration (keepalives and unknown envelopes skipped), `iter_text()`, blocking `wait()` with `WaitLiveRun` fallback, `text()`, `cancel()`, `observe()` resume. |
| `cursor_adapter/_errors.py` | **Errors** — one base class plus a taxonomy mapped from `SdkErrorDetails.sdk_error_code` and Connect codes, preserving `request_id`, `retry_after`, `rate_limit`. |
| `demo.py` | One agent turn through the public surface only. |

The transport is hand-rolled deliberately: the wire protocol is simple enough
(unary = one POST; streams = 5-byte-header frames ending in an
EndStreamResponse) that keeping it visible is worth more in an example than
generated stubs. A [Connect](https://connectrpc.com/) client library works
just as well. Either way, classic gRPC clients will not work — the bridge is
HTTP/1.1 only (see [`docs/protocol.md`](../../docs/protocol.md)).

Not covered here: the adapter-served callback services (custom tools and
custom stores, milestone 6 in the skill), which require running a loopback
Connect *server* — see [`docs/services.md`](../../docs/services.md).

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
and unpacks it to `./cursor-sdk-bridge/`. Alternatively, point the SDK at
any existing bridge launcher with `CURSOR_SDK_BRIDGE_BIN` (or
`Client(bridge_bin=...)`).

## 4. Run one agent turn

```bash
export CURSOR_API_KEY=key_...
python demo.py --workspace /path/to/some/project --prompt "Summarize this repository."
```

Expected output (abridged):

```text
bridge 1.0.0 protocol sdk.v1
agent created: <agent-id> (model <model-id>)
[system] run started: <run-id>
[status RUNNING]
[assistant] This repository contains ...
[tool_call running] read_file
[tool_call completed] read_file
run finished: status=finished duration=8000ms
final result:
This repository contains ...
bridge stopped
```

`demo.py` flags: `--workspace` (agent working directory), `--prompt`,
`--model` (default: first model from `client.cursor.models()`), `--bridge`
(launcher path override).

## Handling failures

Everything the SDK raises derives from `CursorSdkError`, and RPC failures
map `sdk_error_code` onto catchable classes (see
[`docs/errors.md`](../../docs/errors.md)):

```python
from cursor_adapter import NotFoundError, RateLimitError

try:
    info = client.agents.get("agent-that-does-not-exist")
except NotFoundError as err:
    print(err.sdk_error_code, err.request_id)   # AGENT_NOT_FOUND, full ID
except RateLimitError as err:
    time.sleep(err.retry_after or 1.0)
```

A *failed run* is not an exception — the stream still ends normally with a
`RunResult` whose `status` is `"error"` / `"cancelled"` / `"expired"` and
whose `error_message` carries the human-readable reason.
