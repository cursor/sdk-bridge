# The error model

Failed RPCs surface as ordinary Connect/gRPC errors: a transport-level code
(`unauthenticated`, `not_found`, `invalid_argument`, ...) plus a message. The
bridge additionally attaches a structured **error detail** — an
`sdk.v1.SdkErrorDetails` message (from `sdk_errors.proto`) — so adapters can
branch on a stable taxonomy instead of parsing free-form strings.

## Reading the detail

Connect and gRPC both carry typed error details (`google.protobuf.Any` values
keyed by type URL). Look for the detail whose type is
`sdk.v1.SdkErrorDetails`. Connect client libraries expose details directly;
if you are hand-rolling the protocol, a Connect unary error is a non-200
response with a JSON body whose `details[].value` is the unpadded base64 of
the serialized message (see the
[Connect error spec](https://connectrpc.com/docs/protocol#error-end-stream)):

```protobuf
message SdkErrorDetails {
  optional string request_id = 1;                  // full Cursor Cloud request ID
  SdkErrorCode sdk_error_code = 2;                 // stable taxonomy (below)
  string message = 3;                              // human-readable summary
  optional string help_url = 4;                    // docs / remediation link
  optional string provider = 5;                    // upstream provider, when external
  optional google.protobuf.Duration retry_after = 6; // suggested wait before retry
  optional RateLimitInfo rate_limit = 7;           // limit / remaining / reset
}
```

Guidelines:

- **Branch on `sdk_error_code`**, not the message text and not solely the
  transport code. Treat `SDK_ERROR_CODE_UNSPECIFIED` (or a missing detail) as
  an unclassified error.
- **Log the complete `request_id`** when present — Cursor support uses it to
  trace the request. Never truncate it.
- **Honor `retry_after` and `rate_limit`** for `RATE_LIMIT_EXCEEDED` /
  `AGENT_BUSY`-style errors before retrying.
- New enum values may be added to `SdkErrorCode` over time; adapters must
  tolerate values they do not recognize (proto3 keeps unknown enum values
  readable as integers).

## The error-code taxonomy

| Code | Meaning |
| --- | --- |
| `UNAUTHORIZED` | The Cursor API rejected the credentials. |
| `API_KEY_NOT_FOUND` | No API key was provided (no per-call `api_key` and no `CURSOR_API_KEY` in the bridge environment). |
| `PLAN_REQUIRED` | The account's plan does not include the requested feature. |
| `ROLE_FORBIDDEN` | The caller's role does not permit the operation. |
| `FEATURE_UNAVAILABLE` | The feature is not available to this account or context. |
| `AGENT_NOT_FOUND` / `RUN_NOT_FOUND` | Unknown `agent_id` / `run_id`. |
| `VALIDATION_ERROR` | The request failed validation (also used for malformed options). |
| `INVALID_MODEL` | Unknown or unavailable model selection. |
| `INVALID_BRANCH_NAME` | Cloud agent branch name is invalid. |
| `REPOSITORY_REQUIRED` / `REPOSITORY_ACCESS` | Cloud agent repository missing or inaccessible. |
| `PR_RESOLUTION_FAILED` | A referenced pull-request URL could not be resolved. |
| `USAGE_LIMIT_EXCEEDED` | The account hit a usage limit. |
| `AGENT_BUSY` | The agent is already executing a run. |
| `AGENT_ARCHIVED` | The operation is not valid for an archived agent. |
| `RUN_NOT_CANCELLABLE` | The run is already terminal. |
| `RATE_LIMIT_EXCEEDED` | Rate limited; see `retry_after` / `rate_limit`. |
| `UPSTREAM_ERROR` | An upstream provider failed; see `provider`. |
| `INTERNAL_ERROR` | Unexpected bridge or backend failure. |
| `CLIENT_CANCELLED` | The operation was cancelled by the client. |

(Enum values carry the `SDK_ERROR_CODE_` prefix in the proto.)

## Bare `internal error` responses (older bridges)

Bridges released up to and including 1.0.26 mask any unexpected internal
failure as a bare `{"code":"internal","message":"internal error"}` — no
`SdkErrorDetails`, no stderr output. Nothing an adapter does can recover the
underlying message from the outside.

Newer bridges attach the real failure message plus an `SdkErrorDetails`
(`sdk_error_code: INTERNAL_ERROR`) to every unexpected error, and support
`--verbose` / `CURSOR_SDK_BRIDGE_LOG=1` to trace each RPC and full error
(including the underlying stack) on stderr. If you hit a bare
`internal error` during development: upgrade the bridge, turn on `--verbose`,
and re-run the [curl smoke test](smoke-test.md) to separate your adapter
from the bridge.

## Errors outside the detail

Two classes of failure never carry `SdkErrorDetails`:

- **Bridge auth failures** — a missing or wrong bearer token yields a bare
  `UNAUTHENTICATED` Connect error with message `"Unauthorized"`. Fix the
  handshake, don't retry.
- **Process-level failures** — the bridge exiting before the ready line,
  discovery parse failures, or connection refusals are adapter-side launch
  errors. Surface the bridge's captured stderr; it usually explains the
  problem (bad flags, missing token file permissions, port conflicts).

## Run failures are not RPC failures

A run that fails (model error, cancellation, expiry) still ends with a
**successful** stream: you receive a `RunStreamResult` whose `status` is
`ERROR` / `CANCELLED` / `EXPIRED`, optionally with an `error_code` string,
followed by `done`. Reserve RPC-level error handling for transport and
request problems; read run outcomes from the stream (see
[`streaming.md`](streaming.md)).
