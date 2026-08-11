"""JSON serialization helpers for command-line analysis results."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any


def _to_json_compatible(value: Any) -> Any:
    """Convert project result objects to values accepted by ``json.dumps``.

    Result models expose ``to_dict``; the remaining fallbacks make the helper
    safe for dataclasses and scalar types returned by numerical libraries.
    """
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def to_json_string(result: Any) -> str:
    """Serialize an analysis result as readable UTF-8 JSON."""
    payload = result.to_dict() if hasattr(result, "to_dict") else result
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_to_json_compatible)
