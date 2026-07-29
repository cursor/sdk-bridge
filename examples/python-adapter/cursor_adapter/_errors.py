"""The SDK's error taxonomy (see ``docs/errors.md``).

Everything this SDK raises derives from :class:`CursorSdkError`, so callers
catch by class rather than by string matching. Failed RPCs decode the
``sdk.v1.SdkErrorDetails`` Connect error detail and map its ``sdk_error_code``
(falling back to the transport-level Connect code) onto a subclass, keeping
``request_id``, ``retry_after``, and ``rate_limit`` on the exception object.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from ._proto import errors_pb2


class CursorSdkError(Exception):
    """Base class for every error raised by this SDK."""


class BridgeProcessError(CursorSdkError):
    """The bridge process failed to launch, handshake, or shut down."""


class TransportError(CursorSdkError):
    """The HTTP connection to the bridge failed or ended mid-stream."""


@dataclass
class RateLimitInfo:
    """Rate-limit metadata from a failed RPC, when the backend reports it."""

    limit: int | None = None
    remaining: int | None = None
    reset_epoch_seconds: int | None = None


class RpcError(CursorSdkError):
    """A failed RPC: the Connect code plus the bridge's structured detail."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        sdk_error_code: str = "UNSPECIFIED",
        request_id: str | None = None,
        help_url: str | None = None,
        provider: str | None = None,
        retry_after: float | None = None,
        rate_limit: RateLimitInfo | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.sdk_error_code = sdk_error_code
        # Log the complete request_id when reporting errors; Cursor support
        # uses it to trace the request. Never truncate it.
        self.request_id = request_id
        self.help_url = help_url
        self.provider = provider
        self.retry_after = retry_after  # suggested seconds to wait, when known
        self.rate_limit = rate_limit
        text = f"{code}: {message}"
        if sdk_error_code != "UNSPECIFIED":
            text += f" [{sdk_error_code}]"
        if request_id:
            text += f" (request_id={request_id})"
        super().__init__(text)


class AuthenticationError(RpcError):
    """Bridge bearer auth or Cursor API key was missing or rejected."""


class PermissionDeniedError(RpcError):
    """The account, plan, or role does not permit the operation."""


class NotFoundError(RpcError):
    """Unknown ``agent_id`` / ``run_id`` (or other missing resource)."""


class ValidationError(RpcError):
    """The request or agent options failed validation."""


class RateLimitError(RpcError):
    """Rate or usage limit hit; honor ``retry_after`` / ``rate_limit``."""


class AgentBusyError(RpcError):
    """The agent is already executing a run."""


class InvalidStateError(RpcError):
    """The operation is not valid in the resource's current state."""


# sdk_error_code takes precedence: it is the stable taxonomy.
_SDK_CODE_CLASSES: dict[str, type[RpcError]] = {
    "UNAUTHORIZED": AuthenticationError,
    "API_KEY_NOT_FOUND": AuthenticationError,
    "PLAN_REQUIRED": PermissionDeniedError,
    "ROLE_FORBIDDEN": PermissionDeniedError,
    "FEATURE_UNAVAILABLE": PermissionDeniedError,
    "AGENT_NOT_FOUND": NotFoundError,
    "RUN_NOT_FOUND": NotFoundError,
    "VALIDATION_ERROR": ValidationError,
    "INVALID_MODEL": ValidationError,
    "INVALID_BRANCH_NAME": ValidationError,
    "REPOSITORY_REQUIRED": ValidationError,
    "REPOSITORY_ACCESS": PermissionDeniedError,
    "PR_RESOLUTION_FAILED": ValidationError,
    "USAGE_LIMIT_EXCEEDED": RateLimitError,
    "AGENT_BUSY": AgentBusyError,
    "AGENT_ARCHIVED": InvalidStateError,
    "RUN_NOT_CANCELLABLE": InvalidStateError,
    "RATE_LIMIT_EXCEEDED": RateLimitError,
}

# Fallback when no detail is attached (for example bridge bearer-auth
# failures, which are bare UNAUTHENTICATED errors).
_CONNECT_CODE_CLASSES: dict[str, type[RpcError]] = {
    "unauthenticated": AuthenticationError,
    "permission_denied": PermissionDeniedError,
    "not_found": NotFoundError,
    "invalid_argument": ValidationError,
    "resource_exhausted": RateLimitError,
}

_HTTP_STATUS_CODES = {401: "unauthenticated", 403: "permission_denied", 404: "not_found"}


def error_from_connect_body(payload: bytes, http_status: int | None = None) -> RpcError:
    """Build the mapped :class:`RpcError` from a Connect error JSON body.

    The body is ``{"code", "message", "details": [...]}`` where details are
    ``google.protobuf.Any`` values with an unpadded-base64 ``value`` (see the
    Connect error spec and ``docs/errors.md``).
    """
    try:
        body = json.loads(payload)
    except ValueError:
        code = _HTTP_STATUS_CODES.get(http_status or 0, "unknown")
        cls = _CONNECT_CODE_CLASSES.get(code, RpcError)
        return cls(code=code, message=payload.decode(errors="replace"))

    code = body.get("code", "unknown")
    message = body.get("message", "")
    details: errors_pb2.SdkErrorDetails | None = None
    for detail in body.get("details", []):
        if detail.get("type", "").endswith("sdk.v1.SdkErrorDetails"):
            raw = detail.get("value", "")
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            details = errors_pb2.SdkErrorDetails.FromString(decoded)

    if details is None:
        cls = _CONNECT_CODE_CLASSES.get(code, RpcError)
        return cls(code=code, message=message)

    try:
        sdk_code = errors_pb2.SdkErrorCode.Name(details.sdk_error_code).removeprefix(
            "SDK_ERROR_CODE_"
        )
    except ValueError:
        # An enum value from a newer bridge: keep the integer, stay generic.
        sdk_code = str(details.sdk_error_code)
    rate_limit = None
    if details.HasField("rate_limit"):
        info = details.rate_limit
        rate_limit = RateLimitInfo(
            limit=info.limit if info.HasField("limit") else None,
            remaining=info.remaining if info.HasField("remaining") else None,
            reset_epoch_seconds=(
                info.reset_epoch_seconds if info.HasField("reset_epoch_seconds") else None
            ),
        )
    cls = _SDK_CODE_CLASSES.get(sdk_code) or _CONNECT_CODE_CLASSES.get(code, RpcError)
    return cls(
        code=code,
        message=details.message or message,
        sdk_error_code=sdk_code,
        request_id=details.request_id or None,
        help_url=details.help_url or None,
        provider=details.provider or None,
        retry_after=(
            details.retry_after.ToTimedelta().total_seconds()
            if details.HasField("retry_after")
            else None
        ),
        rate_limit=rate_limit,
    )
