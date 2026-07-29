"""Locate the generated ``sdk.v1`` protobuf modules.

Codegen output lives in ``gen/`` next to this package (see ``buf.gen.yaml``
and the README). The generated modules import each other as ``sdk.v1.*``, so
``gen/`` goes on ``sys.path`` instead of being nested inside the package. A
published SDK would ship the generated code inside its own distribution; an
example that regenerates from ``../../proto`` keeps them side by side.
"""

from __future__ import annotations

import os
import sys

_GEN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gen")
if os.path.isdir(_GEN_DIR) and _GEN_DIR not in sys.path:
    sys.path.insert(0, _GEN_DIR)

try:
    from sdk.v1 import sdk_agent_service_pb2 as agent_pb2
    from sdk.v1 import sdk_bridge_control_service_pb2 as control_pb2
    from sdk.v1 import sdk_cursor_service_pb2 as cursor_pb2
    from sdk.v1 import sdk_errors_pb2 as errors_pb2
    from sdk.v1 import sdk_messages_pb2 as messages_pb2
except ImportError as err:  # pragma: no cover - setup guidance, not logic
    raise ImportError(
        "generated sdk.v1 modules not found; run `buf generate` in "
        "examples/python-adapter first (see the README)"
    ) from err

__all__ = ["agent_pb2", "control_pb2", "cursor_pb2", "errors_pb2", "messages_pb2"]
