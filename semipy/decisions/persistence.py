"""Attach / load a DecisionSet on a portal Slot (U7).

The slot owns a plain serialized ``decision_set`` dict (mirroring how it carries
``contract`` and ``ledger``); these helpers convert to and from the typed
:class:`~semipy.decisions.model.DecisionSet`. An unambiguous slot stores nothing,
so absence costs nothing (R3).
"""
from __future__ import annotations

from typing import Any, Optional

from semipy.decisions.model import DecisionSet


def attach_decision_set(slot: Any, decision_set: DecisionSet) -> None:
    """Serialize and store ``decision_set`` on ``slot``. Empty sets store nothing."""
    if decision_set.is_empty() and not decision_set.candidates:
        slot.decision_set = {}
        return
    slot.decision_set = decision_set.to_dict()


def decision_set_for(slot: Any) -> Optional[DecisionSet]:
    """Load the typed DecisionSet from ``slot``, or ``None`` if it has none."""
    raw = getattr(slot, "decision_set", None)
    if not raw:
        return None
    return DecisionSet.from_dict(raw)
