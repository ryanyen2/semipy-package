"""Frontier-kernel Phase 7: lifetime-metrics ledger export."""
from __future__ import annotations

from semipy.contract.access import save_contract
from semipy.contract.models import ContractCase, SlotContract
from semipy.history.version_control import Portal, Slot
from semipy.kernel.ledger import export_portal_ledger, summarize_slot
from semipy.kernel.operators import FreezeCertificate, FreezeEvent, append_freeze_event


def _cert(licensed: bool) -> FreezeCertificate:
    return FreezeCertificate(
        epsilon=0.1, delta=0.05, gamma=1.0, budget_total=1, budget_spent=0,
        held_out_pass_fraction=1.0 if licensed else 0.4, mdl_gain=1.0 if licensed else 0.0,
        licensed=licensed,
    )


def test_summarize_slot_with_no_history_reports_honest_defaults():
    slot = Slot(slot_id="s0", call_site_info={}, function_name_base="f")
    summary = summarize_slot(slot)
    assert summary.freeze_attempts == 0
    assert summary.freeze_licensed_count == 0
    assert summary.case_pass_rate is None  # not "0.0" -- genuinely unknown, no cases evaluated
    assert summary.locality_available is False
    assert summary.regression_count is None


def test_summarize_slot_computes_freeze_trajectory_and_licensed_count():
    slot = Slot(slot_id="s1", call_site_info={}, function_name_base="f")
    append_freeze_event(slot, FreezeEvent(certificate=_cert(False), timestamp=1.0))
    append_freeze_event(slot, FreezeEvent(certificate=_cert(False), timestamp=2.0))
    append_freeze_event(slot, FreezeEvent(certificate=_cert(True), timestamp=3.0))

    summary = summarize_slot(slot)
    assert summary.freeze_attempts == 3
    assert summary.freeze_licensed_count == 1
    assert summary.frozen_fraction_trajectory == [(1.0, False), (2.0, False), (3.0, True)]


def test_summarize_slot_computes_case_pass_rate_from_contract_outcomes():
    slot = Slot(slot_id="s2", call_site_info={}, function_name_base="f")
    contract = SlotContract()
    case = ContractCase(case_id="c1", kind="invariant")
    case.record_outcome(passed=True, commit_id="a")
    case.record_outcome(passed=True, commit_id="b")
    case.record_outcome(passed=False, commit_id="c")
    contract.add(case)
    save_contract(slot, contract)

    summary = summarize_slot(slot)
    assert summary.case_outcome_count == 3
    assert summary.case_pass_rate == 2 / 3


def test_summarize_slot_counts_commits():
    slot = Slot(slot_id="s3", call_site_info={}, function_name_base="f")
    slot.commits = {"c1": object(), "c2": object()}
    summary = summarize_slot(slot)
    assert summary.commit_count == 2


def test_export_portal_ledger_summarizes_every_slot():
    portal = Portal(session_id="sess1", source_file="f.py", module_name="f")
    slot_a = Slot(slot_id="a", call_site_info={}, function_name_base="a")
    slot_b = Slot(slot_id="b", call_site_info={}, function_name_base="b")
    append_freeze_event(slot_a, FreezeEvent(certificate=_cert(True)))
    portal.slots["a"] = slot_a
    portal.slots["b"] = slot_b

    ledger = export_portal_ledger(portal)
    assert ledger["session_id"] == "sess1"
    assert set(ledger["slots"].keys()) == {"a", "b"}
    assert ledger["slots"]["a"]["freeze_licensed_count"] == 1
    assert ledger["slots"]["b"]["freeze_attempts"] == 0
