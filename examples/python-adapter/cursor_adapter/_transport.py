"""Hand-rolled Connect-over-HTTP/1.1 transport on the standard library.

Every RPC is ``POST {base_url}/sdk.v1.<Service>/<Method>`` with a binary
protobuf body and ``Authorization: Bearer <token>`` on **every** request —
unary and streaming (see ``docs/protocol.md``). The bridge serves HTTP/1.1
only, so classic gRPC clients will not work; a Connect client library would
work just as well as this module if you prefer generated stubs.
"""

from __future__ import annotations

import json
import struct
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import IO, TypeVar

from google.protobuf.message import Message

from ._errors import TransportError, error_from_connect_body

_M = TypeVar("_M", bound=Message)


class Transport:
    """Connect protocol client for one authenticated bridge endpoint."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token

    def _post(self, path: str, content_type: str, body: bytes, timeout: float | None):
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "Content-Type": content_type,
                "Authorization": f"Bearer {self._token}",
                "Connect-Protocol-Version": "1",
            },
        )
        return urllib.request.urlopen(request, timeout=timeout)

    def unary(
        self,
        service: str,
        method: str,
        request: Message,
        response_type: type[_M],
        timeout: float | None = 60.0,
    ) -> _M:
        """Unary RPC: one POST, protobuf in, protobuf out.

        Connect unary errors arrive as a non-200 status with a JSON body.
        """
        try:
            with self._post(
                f"/sdk.v1.{service}/{method}",
                "application/proto",
                request.SerializeToString(),
                timeout=timeout,
            ) as reply:
                response = response_type()
                response.ParseFromString(reply.read())
                return response
        except urllib.error.HTTPError as err:
            raise error_from_connect_body(err.read(), http_status=err.code) from None
        except urllib.error.URLError as err:
            raise TransportError(f"{service}/{method}: {err.reason}") from None

    def server_stream(
        self, service: str, method: str, request: Message, response_type: type[_M]
    ) -> Iterator[_M]:
        """Server-streaming RPC (see ``docs/streaming.md``).

        Both directions use enveloped framing: 1 flags byte + 4-byte
        big-endian payload length + payload. The request carries one framed
        message; response frames carry protobuf payloads until a frame with
        flags bit ``0x02`` — the JSON EndStreamResponse, which holds the
        error if the stream failed. The HTTP status is always 200.
        """
        payload = request.SerializeToString()
        body = struct.pack(">BI", 0, len(payload)) + payload
        try:
            # No read timeout: run streams idle during long tool calls, and
            # the bridge sends keepalives only every ~15s.
            reply = self._post(
                f"/sdk.v1.{service}/{method}", "application/connect+proto", body, timeout=None
            )
        except urllib.error.HTTPError as err:
            raise error_from_connect_body(err.read(), http_status=err.code) from None
        except urllib.error.URLError as err:
            raise TransportError(f"{service}/{method}: {err.reason}") from None
        with reply:
            while True:
                flags, length = struct.unpack(">BI", _read_exact(reply, 5))
                payload = _read_exact(reply, length)
                if flags & 0x02:  # EndStreamResponse: JSON, maybe with an error.
                    end = json.loads(payload) if payload else {}
                    if "error" in end:
                        raise error_from_connect_body(json.dumps(end["error"]).encode())
                    return
                message = response_type()
                message.ParseFromString(payload)
                yield message


def _read_exact(stream: IO[bytes], count: int) -> bytes:
    chunks = b""
    while len(chunks) < count:
        try:
            chunk = stream.read(count - len(chunks))
        except OSError as err:
            raise TransportError(f"stream read failed: {err}") from None
        if not chunk:
            raise TransportError("stream ended without an EndStreamResponse frame")
        chunks += chunk
    return chunks
