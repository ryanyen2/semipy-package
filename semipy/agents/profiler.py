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
    """
    Profile DataFrame-like value: schema, shape, and per-column summaries.
    For each column: numeric -> min/max/mean/nunique; otherwise -> distinct value
    sample (value_counts head or unique sample) so the LLM sees variety across
    the data, not just the first row. Data-agnostic; no domain assumptions.
    """
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
        n_cols = len(cols)
        col_budget = max(80, (budget - len("\n".join(lines)) - 200) // max(1, min(n_cols, 25)))
        for c in cols[:25]:
            try:
                s = value[c]
                if hasattr(s, "dtype"):
                    kind = getattr(s.dtype, "kind", str(s.dtype))
                    if kind in ("i", "u", "f", "c"):
                        if hasattr(s, "min") and hasattr(s, "max"):
                            mn = s.min()
                            mx = s.max()
                            me = s.mean() if hasattr(s, "mean") else None
                            nu = s.nunique() if hasattr(s, "nunique") else None
                            parts = [f"  {c}: min={mn}, max={mx}, nunique={nu}"]
                            if me is not None:
                                parts[0] += f", mean={me}"
                            lines.append(parts[0])
                        else:
                            lines.append(f"  {c}: dtype={s.dtype}")
                    else:
                        nunique = s.nunique() if hasattr(s, "nunique") else None
                        try:
                            vc = s.value_counts() if hasattr(s, "value_counts") else None
                            if vc is not None and len(vc) > 0:
                                head = vc.head(12)
                                dist = list(head.items())
                                lines.append(f"  {c}: nunique={nunique}, value_distribution (top)={dist}")
                            else:
                                uniq = s.dropna().unique()[:15].tolist() if hasattr(s, "dropna") else []
                                lines.append(f"  {c}: nunique={nunique}, distinct_sample={uniq}")
                        except Exception:
                            uniq = s.dropna().unique()[:10].tolist() if hasattr(s, "dropna") else []
                            lines.append(f"  {c}: nunique={nunique}, distinct_sample={uniq}")
            except Exception:
                pass
        if hasattr(value, "head"):
            head = value.head(2)
            if hasattr(head, "to_dict"):
                rows = head.to_dict("records")
                lines.append("  sample rows (first 2):")
                for i, row in enumerate(rows[:2]):
                    lines.append(f"    [{i}] {row}")
    except Exception:
        lines.append("  (introspection skipped)")
    return _truncate("\n".join(lines), budget)


def _profile_list_of_dicts(name: str, value: list[dict], budget: int) -> str:
    lines = [f"{name}: type=list[dict], len={len(value)}"]
    if not value:
        return _truncate("\n".join(lines), budget)
    keys = list(value[0].keys())[:30]
    lines.append(f"  keys: {keys}")
    for k in keys[:20]:
        vals = [r.get(k) for r in value[:500] if k in r]
        uniq = list(dict.fromkeys(vals))[:15]
        lines.append(f"  {k}: distinct_sample (n={len(uniq)})={uniq}")
    lines.append("  sample rows (first 2):")
    for i, row in enumerate(value[:2]):
        lines.append(f"    [{i}] {row}")
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


def _is_collection_like(value: Any) -> bool:
    """True if the value is a table/collection the model should see in full (distributions, many values)."""
    if value is None:
        return False
    if hasattr(value, "columns") and hasattr(value, "shape"):
        try:
            r, _ = value.shape
            return r > 1
        except Exception:
            return True
    if isinstance(value, list) and len(value) > 1:
        return True
    if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
        try:
            return len(value) > 1
        except Exception:
            pass
    return False


def profile_runtime_context(
    locals_dict: dict[str, Any],
    variable_values: Optional[dict[str, Any]] = None,
    total_budget: int = 12000,
    per_var_budget: int = 4000,
    collection_budget: int = 7000,
) -> str:
    """
    Profile all relevant variables in scope for LLM context. Puts collection-like
    variables (DataFrames, lists of dicts, etc.) first and gives them most of
    the budget so the model sees full column/value distributions. Scalars are
    labeled as one sample so the model implements for all values, not just that
    one. Data-agnostic; no domain assumptions.
    """
    merged: dict[str, Any] = dict(locals_dict or {})
    if variable_values:
        for k, v in variable_values.items():
            if k not in merged:
                merged[k] = v
    public = {k: v for k, v in merged.items() if not (k.startswith("_") or k.startswith("@"))}
    if not public:
        return "No variables in scope to profile."

    collection_vars = [(n, v) for n, v in public.items() if _is_collection_like(v)]
    scalar_vars = [(n, v) for n, v in public.items() if not _is_collection_like(v)]
    has_collections = len(collection_vars) > 0

    intro: list[str] = []
    if has_collections and scalar_vars:
        intro.append(
            "The function is invoked many times (e.g. once per row). "
            "Any scalar below is one sample; implement so the function works for every value in the data, not only that sample."
        )

    parts: list[str] = []
    used = 0
    budget_for_collections = min(collection_budget, total_budget - 500)
    budget_per_collection = budget_for_collections // max(1, len(collection_vars)) if collection_vars else 0
    for name, value in collection_vars:
        if used >= total_budget:
            break
        part = profile_value(name, value, budget=budget_per_collection)
        if part:
            parts.append(part)
            used += len(part)

    scalar_budget = 200
    for name, value in scalar_vars:
        if used >= total_budget:
            break
        part = profile_value(name, value, budget=scalar_budget)
        if part:
            if has_collections and part.strip().startswith(f"{name}:"):
                part = part.replace(f"{name}:", f"{name}: (one sample; many values will be passed) ", 1)
            parts.append(part)
            used += len(part)

    out = "\n\n".join(intro + parts) if intro else "\n\n".join(parts)
    return out if out else "No variables in scope to profile."
