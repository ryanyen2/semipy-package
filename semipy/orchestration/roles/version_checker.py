"""Version-checker role: prior-version lookup and the reuse/adapt/regenerate route.

The deterministic core wraps the pure ``resolve()`` routing (REUSE / INSTANTIATE /
ADAPT / GENERATE) and projects its ``ResolutionResult`` into a JSON-safe
``VersionContext``. The evidence-grounded LLM reuse *judge* is layered on in U7;
this module owns only the deterministic projection so it is independently testable
and makes zero LLM calls.
"""
from __future__ import annotations

from typing import Any, Optional

from semipy.orchestration.artifacts import VersionContext
from semipy.resolver import resolve


def route(
    portal: Any,
    slot_spec: Any,
    *,
    force_regenerate: bool = False,
    sketch_library: Optional[Any] = None,
) -> VersionContext:
    """Run pure routing and project the result into a typed ``VersionContext``."""
    result = resolve(
        portal,
        slot_spec,
        force_regenerate=force_regenerate,
        sketch_library=sketch_library,
    )
    decision = result.decision
    decision_str = getattr(decision, "value", str(decision))
    return VersionContext(
        decision=decision_str,
        commit_id=result.commit_id,
        parent_commit_ids=list(result.parent_commit_ids or []),
        parent_sources=list(result.parent_sources or []),
        lineage_summary=result.lineage_summary,
        reuse_dispatch_slot_id=result.reuse_dispatch_slot_id,
        sketch_id=result.sketch_id,
        sketch_hole_values=result.sketch_hole_values,
    )
