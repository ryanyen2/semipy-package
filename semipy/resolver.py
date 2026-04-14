"""Resolve SlotSpec to REUSE / INSTANTIATE / ADAPT / GENERATE using spec hash and cross-slot equivalence.

Routing logic lives in semipy.routing.RoutingPolicy. This module is a thin
facade that keeps the public resolve() signature stable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from semipy.types import Decision, SlotSpec


@dataclass
class ResolutionResult:
    decision: Decision
    slot: Any  # Optional[Slot]
    branch_name: Optional[str]
    parent_commit_ids: list[str]
    parent_sources: list[str]
    lineage_summary: Optional[str]
    commit_id: Optional[str]
    """When reusing another slot's dispatch entry, load the function via this slot_id."""
    reuse_dispatch_slot_id: Optional[str] = None
    sketch_id: Optional[str] = None
    sketch_hole_values: Optional[dict[str, str]] = None


def resolve(
    portal: Any,
    slot_spec: SlotSpec,
    *,
    force_regenerate: bool = False,
    sketch_library: Any | None = None,
) -> ResolutionResult:
    """Delegate all routing decisions to RoutingPolicy.

    Resolution precedence (see semipy.routing for the full 10-case order):
    1. No slot in portal → GENERATE
    2. Version lock present → REUSE (locked commit)
    3. force_regenerate → ADAPT from head / donor, or GENERATE
    4. Equivalence matches with commits → REUSE (caller verifies)
    5. No local commits → donor REUSE / sketch INSTANTIATE / GENERATE
    6. Equivalence mismatch with commits → sketch INSTANTIATE / ADAPT
    """
    from semipy.routing import RoutingPolicy
    slot = portal.slots.get(slot_spec.slot_id)
    return RoutingPolicy(portal).decide(
        slot_spec,
        slot,
        force_regenerate=force_regenerate,
        sketch_library=sketch_library,
    )
