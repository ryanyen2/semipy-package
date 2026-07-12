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
    try:
        import pandas as _pd
        if isinstance(value, _pd.DataFrame):
            dtypes_str = ",".join(f"{c}:{str(t)}" for c, t in zip(value.columns, value.dtypes))
            content = _pandas_content_signature(value)
            return f"df:{value.shape}:{hashlib.sha256(dtypes_str.encode()).hexdigest()[:8]}:{content}"
        if isinstance(value, _pd.Series):
            content = _pandas_content_signature(value)
            return f"sr:{value.shape}:{str(value.dtype)}:{content}"
    except ImportError:
        pass
    try:
        import numpy as _np
        if isinstance(value, _np.ndarray):
            return f"np:{value.shape}:{str(value.dtype)}:{hashlib.sha256(value.tobytes()[:512]).hexdigest()[:12]}"
    except ImportError:
        pass
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


def _pandas_content_signature(obj: Any) -> str:
    """A content hash over *all* rows of a DataFrame/Series, not just ``head(5)``.

    ``head(5)`` was the origin §9.1 tail-blindness defect: two same-shape frames
    differing only past row 5 hashed identically, so the reuse fast path skipped
    verify entirely. ``hash_pandas_object`` is a vectorized per-row hash over the
    whole object -- cheap even at 100k rows -- so a tail difference changes the
    signature. Falls back to a stratified head+tail+strided row sample when
    per-row hashing is unavailable (e.g. unhashable object cells)."""
    try:
        import pandas as _pd

        row_hashes = _pd.util.hash_pandas_object(obj, index=True).to_numpy()
        return hashlib.sha256(row_hashes.tobytes()).hexdigest()[:12]
    except Exception:
        return _sampled_content_signature(obj)


def _sampled_content_signature(obj: Any) -> str:
    try:
        n = len(obj)
        if n <= 256:
            sampled = obj
        else:
            step = max(1, n // 246)
            idx = sorted(set(list(range(5)) + list(range(n - 5, n)) + list(range(0, n, step))))[:256]
            sampled = obj.iloc[idx]
        return hashlib.sha256(sampled.to_json().encode()).hexdigest()[:12]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Input profiles (U2, R3): the structural evidence a scope predicate is minted
# from (kernel.guard.synthesize_scope). A profile is JSON-safe and characterizes
# each runtime input's shape: column sets/kinds, per-column null-rate and numeric
# range, and lengths. Scalars profile as ``{"kind": "scalar"}`` and contribute no
# scope conjuncts, so scalar slots keep fingerprint-only behavior.
# ---------------------------------------------------------------------------


def _dtype_kind(dtype: Any) -> str:
    try:
        import pandas as _pd

        if _pd.api.types.is_bool_dtype(dtype):
            return "bool"
        if _pd.api.types.is_numeric_dtype(dtype):
            return "numeric"
        if _pd.api.types.is_datetime64_any_dtype(dtype):
            return "datetime"
        return "string"
    except Exception:
        return "other"


def _numeric_range(series: Any) -> list | None:
    try:
        cmin, cmax = series.min(), series.max()
        # NaN != NaN: an all-null numeric column has no usable range.
        if cmin == cmin and cmax == cmax:  # noqa: PLR0124
            return [float(cmin), float(cmax)]
    except Exception:
        pass
    return None


def _frame_profile(frame: Any) -> dict[str, Any]:
    columns = [str(c) for c in frame.columns]
    n_rows = int(frame.shape[0])
    kinds: dict[str, str] = {}
    null_rates: dict[str, float] = {}
    ranges: dict[str, list] = {}
    for c in frame.columns:
        col = frame[c]
        kind = _dtype_kind(col.dtype)
        kinds[str(c)] = kind
        try:
            null_rates[str(c)] = float(col.isna().mean()) if n_rows else 0.0
        except Exception:
            null_rates[str(c)] = 0.0
        if kind == "numeric":
            rng = _numeric_range(col)
            if rng is not None:
                ranges[str(c)] = rng
    return {
        "kind": "frame",
        "columns": columns,
        "n_rows": n_rows,
        "n_cols": int(frame.shape[1]),
        "column_kinds": kinds,
        "column_null_rates": null_rates,
        "column_ranges": ranges,
    }


def _series_profile(series: Any) -> dict[str, Any]:
    n = int(series.shape[0])
    kind = _dtype_kind(series.dtype)
    prof: dict[str, Any] = {"kind": "series", "len": n, "dtype_kind": kind}
    try:
        prof["null_rate"] = float(series.isna().mean()) if n else 0.0
    except Exception:
        prof["null_rate"] = 0.0
    if kind == "numeric":
        rng = _numeric_range(series)
        if rng is not None:
            prof["range"] = rng
    return prof


def _value_profile(value: Any) -> dict[str, Any]:
    if value is None:
        return {"kind": "none"}
    if isinstance(value, (bool, int, float, str, bytes, bytearray)):
        return {"kind": "scalar", "type": type(value).__name__}
    try:
        import pandas as _pd

        if isinstance(value, _pd.DataFrame):
            return _frame_profile(value)
        if isinstance(value, _pd.Series):
            return _series_profile(value)
    except ImportError:
        pass
    if isinstance(value, (list, tuple, set, frozenset)):
        return {"kind": "collection", "len": len(value)}
    if isinstance(value, dict):
        return {"kind": "mapping", "len": len(value)}
    return {"kind": "other", "type": type(value).__name__}


def compute_input_profile(runtime_values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A JSON-safe structural profile of each runtime input, keyed by free-variable
    name -- the evidence a scope predicate is minted from (R3)."""
    profiles: dict[str, dict[str, Any]] = {}
    for name, value in runtime_values.items():
        if isinstance(name, str) and name.startswith("_"):
            continue
        profiles[str(name)] = _value_profile(value)
    return profiles
