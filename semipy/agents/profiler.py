"""
Duck-typed data profiler for value-aware semiformal generation.

Profiles values by structure (DataFrame-like, list[dict], dict, sequence, scalar)
and produces a bounded string description for LLM context. No domain-specific
logic; purely structural introspection.
"""
from __future__ import annotations

from typing import Any

VALUE_BUDGET = 3000


def _truncate(s: str, budget: int) -> str:
    if len(s) <= budget:
        return s
    return s[: budget - 3] + "..."


def _profile_dataframe(name: str, value: Any, budget: int) -> str:
    lines = [f"{name}: type=DataFrame-like"]
    try:
        cols = list(getattr(value, "columns", []))
        lines.append(f"  columns: {cols}")
        if hasattr(value, "dtypes"):
            try:
                d = getattr(value.dtypes, "to_dict", lambda: dict(value.dtypes))()
                lines.append(f"  dtypes: {d}")
            except Exception:
                pass
        if hasattr(value, "shape"):
            lines.append(f"  shape: {value.shape}")
        if hasattr(value, "head"):
            head = value.head(5)
            if hasattr(head, "to_dict"):
                rows = head.to_dict("records")
                lines.append("  sample rows (first 5):")
                for i, row in enumerate(rows[:5]):
                    lines.append(f"    [{i}] {row}")
        for c in cols[:20]:
            try:
                s = value[c]
                if hasattr(s, "dtype"):
                    kind = getattr(s.dtype, "kind", str(s.dtype))
                    if kind in ("i", "u", "f", "c"):
                        if hasattr(s, "min") and hasattr(s, "max"):
                            lines.append(f"  {c}: min={s.min()}, max={s.max()}, mean={s.mean() if hasattr(s, 'mean') else 'N/A'}, nunique={s.nunique() if hasattr(s, 'nunique') else 'N/A'}")
                        else:
                            lines.append(f"  {c}: dtype={s.dtype}")
                    else:
                        uniq = s.dropna().unique()[:5].tolist() if hasattr(s, "dropna") else []
                        lines.append(f"  {c}: examples={uniq}")
            except Exception:
                pass
    except Exception:
        lines.append("  (introspection skipped)")
    return _truncate("\n".join(lines), budget)


def _profile_list_of_dicts(name: str, value: list[dict], budget: int) -> str:
    lines = [f"{name}: type=list[dict], len={len(value)}"]
    if not value:
        return _truncate("\n".join(lines), budget)
    keys = list(value[0].keys())[:30]
    lines.append(f"  keys: {keys}")
    sample = value[:3]
    lines.append("  sample rows:")
    for i, row in enumerate(sample):
        lines.append(f"    [{i}] {row}")
    for k in keys[:15]:
        vals = [r.get(k) for r in value[:100] if k in r]
        examples = list(dict.fromkeys(vals))[:5]
        lines.append(f"  {k}: examples={examples}")
    return _truncate("\n".join(lines), budget)


def _profile_dict(name: str, value: dict, budget: int) -> str:
    lines = [f"{name}: type=dict, keys={list(value.keys())[:30]}"]
    for k, v in list(value.items())[:25]:
        lines.append(f"  {k!r}: {repr(v)[:80]}")
    return _truncate("\n".join(lines), budget)


def _profile_sequence(name: str, value: Any, budget: int) -> str:
    try:
        n = len(value)
    except Exception:
        n = "?"
    lines = [f"{name}: type=sequence, len={n}"]
    try:
        sample = list(value)[:10]
        lines.append(f"  sample: {sample}")
        types = {}
        for x in list(value)[:100]:
            t = type(x).__name__
            types[t] = types.get(t, 0) + 1
        lines.append(f"  type distribution: {types}")
    except Exception:
        lines.append("  (sample skipped)")
    return _truncate("\n".join(lines), budget)


def _profile_scalar(name: str, value: Any, budget: int) -> str:
    return _truncate(f"{name}: type={type(value).__name__}, value={repr(value)}", budget)


def profile_value(name: str, value: Any, budget: int = VALUE_BUDGET) -> str:
    """
    Data-agnostic introspection via duck typing. Returns a string description
    of the value for LLM context, truncated to budget characters.
    """
    try:
        if hasattr(value, "columns") and hasattr(value, "dtypes") and hasattr(value, "shape"):
            return _profile_dataframe(name, value, budget)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return _profile_list_of_dicts(name, value, budget)
        if isinstance(value, dict) and not (hasattr(value, "columns") or hasattr(value, "shape")):
            return _profile_dict(name, value, budget)
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
            if hasattr(value, "keys") and callable(value.keys):
                try:
                    keys = list(value.keys())[:20]
                    lines = [f"{name}: type=dict-like, keys (sample): {keys}"]
                    for k in list(keys)[:10]:
                        try:
                            lines.append(f"  {k!r}: {repr(value[k])[:60]}")
                        except Exception:
                            pass
                    return _truncate("\n".join(lines), budget)
                except Exception:
                    pass
            return _profile_sequence(name, value, budget)
    except Exception:
        pass
    return _profile_scalar(name, value, budget)
