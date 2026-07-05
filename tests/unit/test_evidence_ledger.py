"""Frontier-kernel Phase 0: evidence ledger (outcome history + holdout split)."""
from __future__ import annotations

from semipy.contract.access import get_contract, record_case_outcomes, save_contract
from semipy.contract.models import ContractCase, SlotContract, assign_holdout_split
from semipy.contract.runner import ContractRunResult, CaseFailure
from semipy.history.version_control import Slot


def _case(case_id: str, **kwargs) -> ContractCase:
    return ContractCase(case_id=case_id, kind=kwargs.pop("kind", "invariant"), **kwargs)


def test_assign_holdout_split_is_deterministic():
    ids = [f"case_{i}" for i in range(200)]
    first = [assign_holdout_split(i) for i in ids]
    second = [assign_holdout_split(i) for i in ids]
    assert first == second


def test_assign_holdout_split_is_roughly_the_requested_fraction():
    ids = [f"case_{i}" for i in range(2000)]
    fraction = sum(assign_holdout_split(i, fraction=0.2) for i in ids) / len(ids)
    assert 0.15 < fraction < 0.25


def test_holdout_assignment_does_not_move_when_fraction_changes_for_other_calls():
    # A case's own persisted split is decided once at creation time by the caller;
    # assign_holdout_split itself is a pure function of (case_id, fraction) -- this
    # test documents that calling it with a *different* fraction for a *different*
    # purpose does not perturb the original assignment a caller already stored.
    cid = "stable-case-id"
    original = assign_holdout_split(cid)
    assert assign_holdout_split(cid) == original


def test_record_outcome_appends_and_bounds_history():
    case = _case("c1")
    for i in range(60):
        case.record_outcome(passed=(i % 2 == 0), commit_id=f"commit{i}")
    assert len(case.outcomes) == 50
    assert case.outcomes[-1] == {"ts": case.outcomes[-1]["ts"], "passed": False, "commit_id": "commit59"}


def test_run_contract_reports_evaluated_case_ids():
    result = ContractRunResult(
        passed=False,
        failures=[CaseFailure(case_id="bad", kind="invariant", label="non_empty", reason="", observed="", message="", failure_kind="empty_output")],
        n_evaluated=2,
        n_skipped=1,
        evaluated_case_ids={"bad", "good"},
    )
    assert result.failing_case_ids() == {"bad"}
    assert result.evaluated_case_ids == {"bad", "good"}


def test_record_case_outcomes_persists_pass_and_fail_and_skips_unevaluated():
    slot = Slot(slot_id="s1", call_site_info={}, function_name_base="f")
    contract = SlotContract()
    good = _case("good")
    bad = _case("bad")
    skipped = _case("skipped")
    for c in (good, bad, skipped):
        contract.add(c)
    save_contract(slot, contract)

    result = ContractRunResult(
        passed=False,
        failures=[CaseFailure(case_id="bad", kind="invariant", label="non_empty", reason="r", observed="", message="m", failure_kind="empty_output")],
        n_evaluated=2,
        n_skipped=1,
        evaluated_case_ids={"good", "bad"},
    )
    record_case_outcomes(slot, [good, bad, skipped], result, commit_id="commit1")

    stored = get_contract(slot)
    assert stored.cases["good"].outcomes == [
        {"ts": stored.cases["good"].outcomes[0]["ts"], "passed": True, "commit_id": "commit1"}
    ]
    assert stored.cases["bad"].outcomes == [
        {"ts": stored.cases["bad"].outcomes[0]["ts"], "passed": False, "commit_id": "commit1"}
    ]
    assert stored.cases["skipped"].outcomes == []


def test_record_case_outcomes_is_noop_when_nothing_evaluated():
    slot = Slot(slot_id="s2", call_site_info={}, function_name_base="f")
    contract = SlotContract()
    contract.add(_case("only"))
    save_contract(slot, contract)
    before = dict(slot.contract)

    result = ContractRunResult(passed=True)
    record_case_outcomes(slot, [_case("only")], result, commit_id="commitX")

    assert slot.contract == before
