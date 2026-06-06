"""Surfacer role: decide WHAT to surface; the deterministic writer applies it.

The surfacer recasts steering synthesis as a named role. It decides what to
surface (delegating the "what" to ``synthesize_steering`` -- carry-forward for
unchanged keys, one LLM call only for changed keys, heuristic fallback when no
key) and returns a typed ``SurfacePlan``. The orchestrator then applies it with
the unchanged, deterministic ``skeleton_writer.surface_skeleton``.

The division of labor is the point (KTD-aligned): the LLM influences only the
*content* of the soft-green intent keys; ``verified`` stays rule-derived
(``_derive_verified``) and ``yields`` stays AST-grounded -- the surfacer never
synthesizes them.
"""
from __future__ import annotations

from typing import Any, Optional

from semipy.agents.steering import synthesize_steering
from semipy.orchestration.artifacts import SurfacePlan

_SCALAR_KEYS = ("intent", "by", "yields", "verified")
_LIST_KEYS = ("given", "unless")


def project_block(block: Any) -> SurfacePlan:
    """Project a ``SteeringBlock`` into the JSON-safe ``SurfacePlan`` artifact."""
    values: dict[str, Any] = {}
    for key in _SCALAR_KEYS:
        entry = getattr(block, key, None)
        values[key] = getattr(entry, "value", "") if entry is not None else ""
    for key in _LIST_KEYS:
        entries = getattr(block, key, None) or []
        values[key] = [getattr(e, "value", "") for e in entries]
    verified_entry = getattr(block, "verified", None)
    verified = getattr(verified_entry, "value", None) if verified_entry is not None else None
    return SurfacePlan(steering_values=values, zones=["P", "E"], verified=verified or None)


def plan_surface(
    spec: Any,
    entry: Any,
    slot: Any,
    prior: Any,
    *,
    promoted_keys: Optional[dict[str, str]] = None,
) -> SurfacePlan:
    """Decide what to surface for this commit and return a typed ``SurfacePlan``.

    Behavior matches the current pipeline: ``synthesize_steering`` carries
    unchanged keys forward, calls the LLM only for changed keys, and falls back to
    heuristics when no key is available. The deterministic write is the caller's
    job (``surface_skeleton``).
    """
    block = synthesize_steering(spec, entry, slot, prior, promoted_keys=promoted_keys)
    return project_block(block)
