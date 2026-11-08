"""Protect provider results before they reach consumers or audit storage."""

from __future__ import annotations

import json
from typing import Any

from backend.agent.hygiene import redact_value

TOOL_RESULT_MAX_BYTES = 64 * 1024


def protect_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a credential-masked copy or a small receipt for omitted output."""
    # Count ASCII-escaped JSON bytes. Check before redacting so large responses
    # never enter the pattern matcher, and again because masking can add bytes.
    size = None
    try:
        size = len(json.dumps(result, allow_nan=False))
        if size <= TOOL_RESULT_MAX_BYTES:
            cleaned, _ = redact_value(result)
            if len(json.dumps(cleaned, allow_nan=False)) <= TOOL_RESULT_MAX_BYTES:
                return cleaned
        message = (
            f"Tool result omitted because it exceeds {TOOL_RESULT_MAX_BYTES} bytes."
        )
    except (TypeError, ValueError, RecursionError, OverflowError):
        message = "Tool result omitted because it could not be safely encoded."

    omitted: dict[str, Any] = {
        "truncated": True,
        "content": [{"type": "text", "text": message}],
    }
    if size is not None:
        omitted["original_size_bytes"] = size
    if "isError" in result:
        omitted["isError"] = bool(result["isError"])
    return omitted
