"""
Data flow tracking: which slots produced which values.

Flow is inferred from code and execution; observation is handled inside the package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.reactivity.reactive import SlotRef


FLOW_ATTR = "_semi_flow"


@dataclass
class DataFlow:
    """Flow record for a value produced by or derived from semiformal slots."""

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
    """Extract observable profile from a result for downstream requirement checks."""
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
    """Build a single flow from multiple input values (merge). Internal use."""
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
    """Duck-typed extraction of flow from a value."""
    return getattr(obj, FLOW_ATTR, None)


class _SemiFlowList(list[Any]):
    """List subclass so producer flow can be stored (built-in list rejects arbitrary attributes)."""


def attach_producer_flow(result: Any, flow: DataFlow) -> Any:
    """
    Store *flow* on *result* for dependency tracking.

    Plain ``list`` instances cannot take new attributes; those are wrapped in
    ``_SemiFlowList`` and the same flow is attached to each element when
    ``setattr`` succeeds, so downstream slots that receive a single row still
    see upstream provenance.
    """
    if result is None:
        return result
    try:
        setattr(result, FLOW_ATTR, flow)
        return result
    except (TypeError, AttributeError):
        pass
    if isinstance(result, list):
        wrapped = _SemiFlowList(result)
        setattr(wrapped, FLOW_ATTR, flow)
        for item in wrapped:
            try:
                setattr(item, FLOW_ATTR, flow)
            except (TypeError, AttributeError):
                pass
        return wrapped
    return result
