"""Stable fingerprints of slot runtime_values for reuse verification."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _fingerprint_value(value: Any) -> str:
    if value is None:
        return "n"
    if isinstance(value, bool):
        return "b:1" if value else "b:0"
    if isinstance(value, int):
        return f"i:{value}"
    if isinstance(value, float):
        return f"f:{repr(value)}"
    if isinstance(value, str):
        return f"s:{json.dumps(value, ensure_ascii=True)}"
    if isinstance(value, (bytes, bytearray)):
        return f"y:{hashlib.sha256(bytes(value)).hexdigest()[:16]}"
    if isinstance(value, (list, tuple)):
        inner = ",".join(_fingerprint_value(x) for x in value)
        return f"L:{len(value)}:[{inner}]"
    if isinstance(value, dict):
        parts = []
        for k in sorted(value.keys(), key=lambda x: repr(x)):
            parts.append(f"{_fingerprint_value(k)}={_fingerprint_value(value[k])}")
        return "D:{" + ",".join(parts) + "}"
    return f"r:{type(value).__name__}:{repr(value)}"


def compute_runtime_input_fingerprint(runtime_values: dict[str, Any]) -> str:
    """
    Short stable hash of runtime_values for comparing invocations of the same template.

    Keys are sorted lexicographically so order of insertion does not matter.
    """
    if not runtime_values:
        return hashlib.sha256(b"{}").hexdigest()[:16]
    keys = sorted(runtime_values.keys(), key=str)
    parts = [f"{k}={_fingerprint_value(runtime_values[k])}" for k in keys]
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
