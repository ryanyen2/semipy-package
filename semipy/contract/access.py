"""Bridge between the persisted ``Slot.contract`` dict and the ``SlotContract``
object, plus small mutation helpers. All reads/writes go through here so the
serialization format stays in one place.
"""
from __future__ import annotations

from typing import Any

from semipy.contract.models import ContractCase, SlotContract
from semipy.contract.serialize import contract_from_dict, contract_to_dict


def get_contract(slot: Any) -> SlotContract:
    """Return the slot's SlotContract (empty if none recorded yet)."""
    return contract_from_dict(getattr(slot, "contract", {}) or {})


def save_contract(slot: Any, contract: SlotContract) -> None:
    """Persist a SlotContract back onto the slot (caller saves the portal)."""
    slot.contract = contract_to_dict(contract)


def load_active_cases(slot: Any) -> list[ContractCase]:
    """Active behavioral cases for the slot's acceptance gate."""
    return get_contract(slot).active()


def quarantine_cases(slot: Any, case_ids: list[str], why: str) -> None:
    """Mark cases quarantined (kept for audit, not enforced) and persist on the slot."""
    if not case_ids:
        return
    contract = get_contract(slot)
    for cid in case_ids:
        contract.quarantine(cid, why)
    save_contract(slot, contract)


def record_case_outcomes(slot: Any, cases: list[ContractCase], result: Any, *, commit_id: str) -> None:
    """Persist a pass/fail outcome for every case ``result`` actually replayed.

    ``result`` is a ``ContractRunResult``; only cases in its ``evaluated_case_ids``
    were actually run (a skipped case leaves no outcome). No-op if nothing was
    evaluated, so a disabled/no-op contract gate never touches the portal.
    """
    evaluated_ids: set[str] = getattr(result, "evaluated_case_ids", None) or set()
    if not evaluated_ids:
        return
    failing_ids = result.failing_case_ids()
    contract = get_contract(slot)
    changed = False
    for case in cases:
        if case.case_id not in evaluated_ids:
            continue
        stored = contract.cases.get(case.case_id)
        if stored is None:
            continue
        stored.record_outcome(passed=case.case_id not in failing_ids, commit_id=commit_id)
        changed = True
    if changed:
        save_contract(slot, contract)


def retire_active_cases(slot: Any, why: str) -> int:
    """Retire (quarantine) every active case on the slot; returns how many.

    Used when the slot's meaning changes (spec/signature edit): cases derived under
    the old meaning must not be enforced against the new one, or the gate would
    fight the user's intent. Cases are kept (superseded audit trail) and the
    maintainer re-seeds under the new spec — content-addressing reactivates any
    still-valid invariant while a stale example stays retired.
    """
    contract = get_contract(slot)
    active = contract.active()
    if not active:
        return 0
    for c in active:
        contract.quarantine(c.case_id, why)
    save_contract(slot, contract)
    return len(active)
