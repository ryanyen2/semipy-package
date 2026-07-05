"""Lifetime-metrics ledger export (Phase 7): the instrumentation to ever run
the paper's evaluation, per §6 -- "Evaluation design itself is deferred per
scope, but the instrumentation to *ever* run it lands here."

Reports exactly what is actually persisted today: the freeze-attempt history
(``Slot.freeze_events``, Phase 3-4) and the contract's per-case outcome
history (``Slot.contract``, Phase 0). Locality and regression-count need a
live melt/blame call site to ever produce data -- ``melt`` is additive, not
yet wired into anything (Phase 4) -- so those are reported honestly as
unavailable rather than fabricated; the same discipline as Phase 1's
multi-node-fraction caveat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.contract.access import get_contract
from semipy.kernel.operators import get_freeze_events


@dataclass
class SlotLedgerSummary:
    """One slot's lifetime metrics, computable from what is persisted today."""

    slot_id: str
    frozen_fraction_trajectory: list[tuple[float, bool]] = field(default_factory=list)
    freeze_attempts: int = 0
    freeze_licensed_count: int = 0
    case_pass_rate: Optional[float] = None
    case_outcome_count: int = 0
    commit_count: int = 0
    locality_available: bool = False
    regression_count: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "frozen_fraction_trajectory": [
                {"ts": ts, "licensed": licensed} for ts, licensed in self.frozen_fraction_trajectory
            ],
            "freeze_attempts": self.freeze_attempts,
            "freeze_licensed_count": self.freeze_licensed_count,
            "case_pass_rate": self.case_pass_rate,
            "case_outcome_count": self.case_outcome_count,
            "commit_count": self.commit_count,
            "locality_available": self.locality_available,
            "regression_count": self.regression_count,
        }


def summarize_slot(slot: Any) -> SlotLedgerSummary:
    """Compute one slot's lifetime metrics from its persisted history."""
    events = get_freeze_events(slot)
    trajectory = [(e.timestamp, e.certificate.licensed) for e in events]
    licensed_count = sum(1 for e in events if e.certificate.licensed)

    total_outcomes = 0
    passed_outcomes = 0
    for case in get_contract(slot).cases.values():
        total_outcomes += len(case.outcomes)
        passed_outcomes += sum(1 for o in case.outcomes if o.get("passed"))
    case_pass_rate = (passed_outcomes / total_outcomes) if total_outcomes else None

    return SlotLedgerSummary(
        slot_id=getattr(slot, "slot_id", ""),
        frozen_fraction_trajectory=trajectory,
        freeze_attempts=len(events),
        freeze_licensed_count=licensed_count,
        case_pass_rate=case_pass_rate,
        case_outcome_count=total_outcomes,
        commit_count=len(getattr(slot, "commits", None) or {}),
    )


def export_portal_ledger(portal: Any) -> dict[str, Any]:
    """The lifetime-metrics ledger for every slot in a portal, JSON-safe."""
    slots = getattr(portal, "slots", None) or {}
    return {
        "session_id": getattr(portal, "session_id", ""),
        "slots": {slot_id: summarize_slot(slot).to_dict() for slot_id, slot in slots.items()},
    }
