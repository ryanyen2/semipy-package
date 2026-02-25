"""
Data flow tracking: semipy observes values and tracks which slots produced them.

Flow is inferred from code and execution; observation and subscriber logic are
handled inside the package. No user setup. Variables are tracked automatically
when they pass through semi(); the dependency graph and staleness are updated
from that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.reactive import SlotRef


FLOW_ATTR = "_semi_flow"


@dataclass
class DataFlow:
    """
    Flow record for a value produced by or derived from semiformal slots.
    Used internally to build the dependency graph and validate downstream requirements.
    """

    producing_slot: SlotRef
    producing_commit_id: str
    upstream_chain: list[SlotRef] = field(default_factory=list)
    output_profile: dict[str, Any] = field(default_factory=dict)


def _profile_sequence_of_dicts(obj: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {"type": "list", "element": "dict"}
    try:
        if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
            items = list(obj)
            if items:
                first = items[0]
                if isinstance(first, dict):
                    profile["columns"] = list(first.keys())
                elif hasattr(first, "keys") and callable(getattr(first, "keys")):
                    profile["columns"] = list(first.keys())
    except Exception:
        pass
    return profile


def _profile_dataframe_like(obj: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {"type": "dataframe_like"}
    try:
        if hasattr(obj, "columns"):
            cols = getattr(obj, "columns", None)
            if cols is not None:
                profile["columns"] = list(cols) if hasattr(cols, "__iter__") else []
    except Exception:
        pass
    return profile


def _profile_mapping(obj: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {"type": "dict"}
    try:
        if hasattr(obj, "keys") and callable(getattr(obj, "keys")):
            profile["keys"] = list(obj.keys())
    except Exception:
        pass
    return profile


def profile_output(result: Any) -> dict[str, Any]:
    """
    Extract observable profile from a result for downstream requirement checks.
    Data-agnostic: uses duck typing (columns, keys, type).
    """
    if result is None:
        return {"type": "none"}
    if isinstance(result, dict):
        out = _profile_mapping(result)
        if not out.get("keys"):
            out["keys"] = list(result.keys())
        return out
    if isinstance(result, list):
        if result and isinstance(result[0], dict):
            return _profile_sequence_of_dicts(result)
        return {"type": "list", "length": len(result)}
    if hasattr(result, "columns"):
        return _profile_dataframe_like(result)
    if hasattr(result, "__iter__") and not isinstance(result, (str, bytes)):
        try:
            first = next(iter(result), None)
            if first is not None and isinstance(first, dict):
                return _profile_sequence_of_dicts(result)
        except Exception:
            pass
    return {"type": type(result).__name__}


def create_flow(
    session_id: str,
    slot_id: str,
    commit_id: str,
    upstream_chain: Optional[list[SlotRef]] = None,
    output_profile: Optional[dict[str, Any]] = None,
) -> DataFlow:
    return DataFlow(
        producing_slot=SlotRef(session_id=session_id, slot_id=slot_id),
        producing_commit_id=commit_id,
        upstream_chain=upstream_chain or [],
        output_profile=output_profile or {},
    )


def _flow_from_inputs(*values: Any) -> Optional[DataFlow]:
    """
    Internal: build a single flow from multiple input values (e.g. merge).
    Used when a value is derived from several inputs that may each have flow.
    Not part of the public API; observation is automatic from code and execution.
    """
    flows = [getattr(v, FLOW_ATTR, None) for v in values]
    valid = [f for f in flows if f is not None]
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    chain: list[SlotRef] = []
    for f in valid:
        chain.extend(f.upstream_chain)
        chain.append(f.producing_slot)
    return DataFlow(
        producing_slot=valid[0].producing_slot,
        producing_commit_id="",
        upstream_chain=chain,
        output_profile={},
    )


def extract_flow(obj: Any) -> Optional[DataFlow]:
    """Duck-typed extraction of flow from a value. Used by semipy to track variables."""
    return getattr(obj, FLOW_ATTR, None)
