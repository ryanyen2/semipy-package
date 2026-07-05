"""Frontier-kernel Phase 2: population as the engine (execution-ranked head selection)."""
from __future__ import annotations

from semipy.contract.models import ContractCase, SlotContract
from semipy.contract.access import save_contract
from semipy.contract.runner import CaseFailure, ContractRunResult
from semipy.decisions.cluster import UNRUNNABLE, cluster_signatures
from semipy.decisions.divergence import CandidateRun, DivergenceResult
from semipy.history.version_control import Slot
from semipy.kernel.population import (
    build_population,
    score_candidates_against_contract,
    select_head,
)


def _divergence(runs_by_id: dict[str, tuple[str, tuple[str, ...], str | None]]) -> DivergenceResult:
    """Build a DivergenceResult from {candidate_id: (source, signature, error)}."""
    runs = {
        cid: CandidateRun(candidate_id=cid, source=src, signature=sig, error=err)
        for cid, (src, sig, err) in runs_by_id.items()
    }
    clusters = cluster_signatures({cid: r.signature for cid, r in runs.items()})
    return DivergenceResult(clusters=clusters, runs=runs, mode="pure")


def test_build_population_marks_unrunnable_and_errored_candidates_type_invalid():
    div = _divergence(
        {
            "c0": ("return 1", ("ok:1",), None),
            "c1": ("raise", (UNRUNNABLE,), "boom"),
        }
    )
    particles = {p.candidate_id: p for p in build_population(div)}
    assert particles["c0"].type_ok is True
    assert particles["c1"].type_ok is False


def test_select_head_falls_back_to_heaviest_cluster_without_contract_signal():
    # Majority-vote regression check: 2 candidates agree, 1 disagrees; with no
    # contract signal the heaviest (agreeing) cluster must still win, matching
    # today's _default_head behavior.
    div = _divergence(
        {
            "c0": ("return 1", ("ok:1",), None),
            "c1": ("return 1", ("ok:1",), None),
            "c2": ("return 2", ("ok:2",), None),
        }
    )
    head_id, head_src = select_head(div)
    assert head_id in ("c0", "c1")
    assert head_src == "return 1"


def test_select_head_prefers_higher_contract_pass_fraction_within_same_cluster():
    div = _divergence(
        {
            "c0": ("return 1", ("ok:1",), None),
            "c1": ("return 1", ("ok:1",), None),
        }
    )
    head_id, head_src = select_head(div, contract_pass_fractions={"c0": 0.5, "c1": 1.0})
    assert (head_id, head_src) == ("c1", "return 1")


def test_select_head_never_picks_a_type_invalid_candidate_over_a_valid_one():
    div = _divergence(
        {
            "valid": ("return 1", ("ok:1",), None),
            "broken": ("raise", (UNRUNNABLE,), "boom"),
        }
    )
    # Even if the broken candidate is (implausibly) handed a perfect contract
    # score, type validity is a hard gate: it must never win.
    head_id, _ = select_head(div, contract_pass_fractions={"broken": 1.0, "valid": 0.0})
    assert head_id == "valid"


def test_select_head_returns_none_when_no_candidates():
    div = DivergenceResult(clusters=[], runs={}, mode="pure")
    assert select_head(div) == (None, None)


def test_select_head_is_deterministic_on_full_ties():
    div = _divergence(
        {
            "cB": ("return 1", ("ok:1",), None),
            "cA": ("return 1", ("ok:1",), None),
        }
    )
    head_id, _ = select_head(div)
    assert head_id == "cB"  # tie broken by candidate id, deterministic either way


class _Config:
    def __init__(self, *, contract_enabled: bool = True, contract_max_cases: int = 25):
        self.contract_enabled = contract_enabled
        self.contract_max_cases = contract_max_cases


def test_score_candidates_against_contract_returns_none_when_contracts_disabled():
    slot = Slot(slot_id="s1", call_site_info={}, function_name_base="f")
    result = score_candidates_against_contract(
        {"c0": "return 1"}, slot=slot, slot_spec=object(), config=_Config(contract_enabled=False),
    )
    assert result is None


def test_score_candidates_against_contract_returns_none_when_no_active_cases():
    slot = Slot(slot_id="s2", call_site_info={}, function_name_base="f")
    save_contract(slot, SlotContract())
    result = score_candidates_against_contract(
        {"c0": "return 1"}, slot=slot, slot_spec=object(), config=_Config(),
    )
    assert result is None


def test_score_candidates_against_contract_computes_per_candidate_fraction(monkeypatch):
    slot = Slot(slot_id="s3", call_site_info={}, function_name_base="f")
    contract = SlotContract()
    contract.add(ContractCase(case_id="a", kind="invariant"))
    contract.add(ContractCase(case_id="b", kind="invariant"))
    save_contract(slot, contract)

    def _fake_run_contract(*, implementation_source, slot_spec, cases, scaffold_source=None, timeout=15):
        if implementation_source == "good":
            return ContractRunResult(passed=True, evaluated_case_ids={"a", "b"})
        return ContractRunResult(
            passed=False,
            failures=[CaseFailure(case_id="a", kind="invariant", label="x", reason="", observed="", message="", failure_kind="type_mismatch")],
            evaluated_case_ids={"a", "b"},
        )

    monkeypatch.setattr("semipy.contract.runner.run_contract", _fake_run_contract)

    fractions = score_candidates_against_contract(
        {"good": "good", "half": "half"}, slot=slot, slot_spec=object(), config=_Config(),
    )
    assert fractions == {"good": 1.0, "half": 0.5}


def test_score_candidates_against_contract_excludes_candidates_that_never_replayed(monkeypatch):
    slot = Slot(slot_id="s4", call_site_info={}, function_name_base="f")
    contract = SlotContract()
    contract.add(ContractCase(case_id="a", kind="invariant"))
    save_contract(slot, contract)

    def _fake_run_contract(*, implementation_source, slot_spec, cases, scaffold_source=None, timeout=15):
        return ContractRunResult(passed=True, evaluated_case_ids=set())

    monkeypatch.setattr("semipy.contract.runner.run_contract", _fake_run_contract)

    fractions = score_candidates_against_contract(
        {"unrunnable": "raise"}, slot=slot, slot_spec=object(), config=_Config(),
    )
    assert fractions == {}
