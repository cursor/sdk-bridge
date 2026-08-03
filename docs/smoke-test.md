# Protocol smoke test (curl, no adapter code)

When something fails while you are building an adapter, the first question is
always *"is it my code or the bridge?"*. This page is the oracle: a complete
spawn → `Ping` → `GetVersion` → `Me` → `CreateAgent` → `Send` sequence in
JSON mode using nothing but a shell and `curl`. If a step fails here too, the
problem is on the bridge side (or in your key / environment); if it works
here but not through your adapter, diff your adapter's raw request against
the one below.

Every command was verified against a real bridge. Run them from any
directory; they assume a POSIX shell with `curl`, `python3` (JSON
extraction only), and `xxd` (streaming request framing only).

## 1. Spawn the bridge and capture the endpoint

```bash
export CURSOR_API_KEY=key_...        # from https://cursor.com/dashboard
./cursor-sdk-bridge/bin/cursor-sdk-bridge --workspace "$PWD" --port 39217 \
  2> /tmp/bridge-stderr.log &
sleep 2
B=http://127.0.0.1:39217
TOKEN=$(cat "$(grep -o '"authTokenFile":"[^"]*"' /tmp/bridge-stderr.log | cut -d'"' -f4)")
```

(A fixed `--port` keeps the one-liners copy-pasteable; production adapters
should use the default ephemeral port and parse the ready line properly —
see [`protocol.md`](protocol.md). Add `--verbose` to make the bridge log
every RPC and full error to stderr.)

## 2. Ping and GetVersion — is the bridge alive and speaking sdk.v1?

```bash
curl -s -X POST "$B/sdk.v1.SdkBridgeControlService/Ping" \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" -d '{}'
# -> {"message":"pong"}

curl -s -X POST "$B/sdk.v1.SdkBridgeControlService/GetVersion" \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" -d '{}'
# -> {"bridgeVersion":"1.0.0","protocolVersion":"sdk.v1","capabilities":[...]}
```

A `{"code":"unauthenticated","message":"Unauthorized"}` here means your
bearer token is wrong — re-read `authTokenFile` and trim whitespace.

## 3. Me — is the API key valid?

Catalog calls require a per-call `api_key`; the bridge never falls back to
its environment for these:

```bash
curl -s -X POST "$B/sdk.v1.SdkCursorService/Me" \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d "{\"options\":{\"apiKey\":\"$CURSOR_API_KEY\"}}"
# -> {"user":{"apiKeyName":...,"userEmail":...}}
```

Omitting the key yields
`{"code":"unauthenticated","message":"API key is required for cloud catalog calls."}`.

## 4. CreateAgent — can the bridge create a local agent?

Set the key on `options.apiKey` (see [`protocol.md`](protocol.md) on why the
env var alone is not sufficient):

```bash
curl -s -X POST "$B/sdk.v1.SdkAgentService/CreateAgent" \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d "{\"options\":{\"model\":{\"id\":\"composer-2\"},\"apiKey\":\"$CURSOR_API_KEY\",\"local\":{\"cwd\":[\"$PWD\"]}}}"
# -> {"agentId":"agent-...","model":{"id":"composer-2"}}
AGENT=agent-...   # paste the returned id
```

(Use a model id from `SdkCursorService.ListModels` — same shape as `Me`
above.)

## 5. Send — does a run stream?

`Send` is a server-streaming RPC, so the request uses the Connect streaming
content type and each message is framed as 1 flags byte + a 4-byte big-endian
length + the payload ([Connect streaming spec](https://connectrpc.com/docs/protocol#streaming-rpcs)).
Building that frame by hand:

```bash
BODY="{\"agentId\":\"$AGENT\",\"message\":{\"text\":\"Say hello.\"}}"
{ printf '\x00'; printf '%s' "$BODY" | wc -c | xargs printf '%08x' | xxd -r -p; printf '%s' "$BODY"; } > /tmp/send.bin

curl -sN -X POST "$B/sdk.v1.SdkAgentService/Send" \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/connect+json" \
  --data-binary @/tmp/send.bin | tr -c '[:print:]\n' '.'
```

The response interleaves 5-byte binary frame headers (rendered as `.` by the
`tr`) with JSON envelopes: `sdkMessage` events, then `result`, then `done`,
then the end-of-stream frame (flags `0x02`) whose JSON carries
`{"error":{...}}` if and only if the RPC failed. A failed *run* still ends as a successful
stream — the failure text arrives in the `status` event payload (see
[`streaming.md`](streaming.md)).

## 6. Shut down

```bash
curl -s -X POST "$B/sdk.v1.SdkBridgeControlService/Shutdown" \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" -d '{}'
```

## Reading failures

- Every error body is a Connect JSON error: `{"code":..., "message":...,
  "details":[...]}` with the `sdk.v1.SdkErrorDetails` detail base64-encoded in
  `details[].value` (see [`errors.md`](errors.md)).
- A bare `{"code":"internal","message":"internal error"}` with no details is
  the signature of an older bridge masking an unexpected internal
  failure. Current bridges return the real message plus `SdkErrorDetails`, and
  `--verbose` / `CURSOR_SDK_BRIDGE_LOG=1` traces every RPC on stderr. If you
  are stuck on an older bridge, the masked exception is unrecoverable from
  the outside — upgrade first, then re-run this smoke test.
