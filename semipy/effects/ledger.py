"""The Effect Ledger: an append-only, per-slot log of applied real-world effects.

Co-versioned with the implementation DAG -- each :class:`LedgerEvent` is keyed by
``(slot_id, origin_commit_id, invocation_id)`` and stores the *materialized*
applied effects plus their compensations, so a revert replays exact inverses and
never re-derives them from a (regenerable, possibly non-identical) implementation.

Persisted as a plain dict on ``Slot.ledger`` following the contract subsystem's
serialize idiom (values coerced through ``to_json_safe``); the history layer stays
dependency-light. Effect (de)serialization is recursive because an effect carries
its compensation (an inverse effect).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.effects.models import Effect, LedgerEvent


def _json_safe(v: Any) -> Any:
    from semipy.contract.serialize import to_json_safe

    return to_json_safe(v)


def effect_to_dict(e: Effect) -> dict[str, Any]:
    return {
        "op": e.op,
        "target": e.target,
        "payload": _json_safe(e.payload),
        "selector": _json_safe(e.selector) if e.selector else None,
        "compensation": effect_to_dict(e.compensation) if e.compensation else None,
        "provenance": _json_safe(e.provenance),
        "effect_id": e.effect_id,
    }


def effect_from_dict(d: dict[str, Any]) -> Effect:
    comp = d.get("compensation")
    return Effect(
        op=d.get("op", "call"),
        target=d.get("target", ""),
        payload=dict(d.get("payload") or {}),
        selector=(dict(d["selector"]) if d.get("selector") else None),
        compensation=(effect_from_dict(comp) if isinstance(comp, dict) else None),
        provenance=dict(d.get("provenance") or {}),
        effect_id=d.get("effect_id", ""),
    )


def event_to_dict(ev: LedgerEvent) -> dict[str, Any]:
    return {
        "event_id": ev.event_id,
        "slot_id": ev.slot_id,
        "origin_commit_id": ev.origin_commit_id,
        "invocation_id": ev.invocation_id,
        "applied_effects": [effect_to_dict(e) for e in ev.applied_effects],
        "compensations": [effect_to_dict(e) for e in ev.compensations],
        "artifact_snapshot_ref": ev.artifact_snapshot_ref,
        "contract_case_ids": list(ev.contract_case_ids),
        "status": ev.status,
        "timestamp": ev.timestamp,
        "parent_event_id": ev.parent_event_id,
    }


def event_from_dict(d: dict[str, Any]) -> LedgerEvent:
    return LedgerEvent(
        event_id=d.get("event_id", ""),
        slot_id=d.get("slot_id", ""),
        origin_commit_id=d.get("origin_commit_id", ""),
        invocation_id=d.get("invocation_id", ""),
        applied_effects=[effect_from_dict(e) for e in d.get("applied_effects", []) if isinstance(e, dict)],
        compensations=[effect_from_dict(e) for e in d.get("compensations", []) if isinstance(e, dict)],
        artifact_snapshot_ref=d.get("artifact_snapshot_ref", ""),
        contract_case_ids=list(d.get("contract_case_ids", []) or []),
        status=d.get("status", "applied"),
        timestamp=float(d.get("timestamp", 0.0) or 0.0),
        parent_event_id=d.get("parent_event_id", ""),
    )


@dataclass
class EffectLedger:
    version: int = 1
    events: list[LedgerEvent] = field(default_factory=list)

    def append(self, ev: LedgerEvent) -> LedgerEvent:
        self.events.append(ev)
        self.version += 1
        return ev

    def latest(self) -> Optional[LedgerEvent]:
        return self.events[-1] if self.events else None

    def applied(self) -> list[LedgerEvent]:
        return [e for e in self.events if e.status == "applied"]

    def reverted(self) -> list[LedgerEvent]:
        return [e for e in self.events if e.status == "reverted"]

    def find(self, event_id: str) -> Optional[LedgerEvent]:
        return next((e for e in self.events if e.event_id == event_id), None)


def ledger_to_dict(ledger: EffectLedger) -> dict[str, Any]:
    return {"version": int(ledger.version), "events": [event_to_dict(e) for e in ledger.events]}


def ledger_from_dict(d: dict[str, Any] | None) -> EffectLedger:
    if not isinstance(d, dict):
        return EffectLedger()
    events: list[LedgerEvent] = []
    for e in d.get("events", []) or []:
        if isinstance(e, dict):
            try:
                events.append(event_from_dict(e))
            except Exception:
                continue
    return EffectLedger(version=int(d.get("version", 1) or 1), events=events)


# -- per-slot access (mirrors contract/access.py) --------------------------
def get_ledger(slot: Any) -> EffectLedger:
    return ledger_from_dict(getattr(slot, "ledger", {}) or {})


def save_ledger(slot: Any, ledger: EffectLedger) -> None:
    slot.ledger = ledger_to_dict(ledger)


def append_event(slot: Any, event: LedgerEvent) -> LedgerEvent:
    ledger = get_ledger(slot)
    ledger.append(event)
    save_ledger(slot, ledger)
    return event
