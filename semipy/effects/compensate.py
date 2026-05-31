"""Revert applied effects by replaying their materialized compensations (Saga).

A revert NEVER re-derives inverses from the (regenerable, possibly non-identical)
implementation -- it replays the compensations captured at apply time, in reverse
order, through the registered backends. This directly addresses the "semantic
rollback" hazard: what is undone is exactly what was done.
"""
from __future__ import annotations

from typing import Any

from semipy.effects.backends import resolve_backend


def _mutating_effects(target: Any) -> list:
    """Pull the ordered mutating effects out of an EffectResult or LedgerEvent."""
    from semipy.effects.models import EffectResult, LedgerEvent

    if isinstance(target, EffectResult):
        return [e for e in target.effect_script.effects if e.is_mutating()]
    if isinstance(target, LedgerEvent):
        return list(target.applied_effects)
    raise TypeError(f"revert expects an EffectResult or LedgerEvent, got {type(target).__name__}")


def revert(target: Any) -> int:
    """Undo ``target`` (an :class:`EffectResult` or :class:`LedgerEvent`).

    Replays each effect's stored compensation in reverse order. Returns the number
    of compensations applied. An effect without a compensation is skipped (it was
    judged irreversible at gate time, so it should never have been applied).
    """
    effects = _mutating_effects(target)
    applied = 0
    for eff in reversed(effects):
        comp = eff.compensation
        if comp is None:
            continue
        backend = resolve_backend(comp.target)
        shadow = backend.open_shadow(comp.target)
        try:
            backend.apply(shadow, comp)
            backend.commit(shadow)
            applied += 1
        except Exception:
            backend.discard(shadow)
            raise
    return applied


def revert_ledger_event(slot: Any, event_id: str) -> int:
    """Durable revert: find ``event_id`` in the slot's ledger, replay its
    compensations, and append a ``reverted`` event (append-only audit trail).

    The caller persists the portal afterwards.
    """
    from semipy.effects.ledger import append_event, get_ledger
    from semipy.effects.models import LedgerEvent, compute_event_id

    ledger = get_ledger(slot)
    ev = ledger.find(event_id)
    if ev is None:
        raise KeyError(f"no ledger event {event_id!r} on slot")
    if ev.status == "reverted":
        return 0
    count = revert(ev)
    ev.status = "reverted"
    seq = len(ledger.events)
    rev = LedgerEvent(
        event_id=compute_event_id(
            slot_id=ev.slot_id, origin_commit_id=ev.origin_commit_id,
            invocation_id=ev.invocation_id, seq=seq,
        ),
        slot_id=ev.slot_id,
        origin_commit_id=ev.origin_commit_id,
        invocation_id=ev.invocation_id,
        applied_effects=list(ev.compensations),
        compensations=[],
        status="reverted",
        parent_event_id=ev.event_id,
    )
    # re-persist both the status change and the new event
    from semipy.effects.ledger import save_ledger
    save_ledger(slot, ledger)        # persists ev.status = "reverted"
    append_event(slot, rev)
    return count
