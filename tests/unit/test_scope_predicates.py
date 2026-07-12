"""U2 (R3-R4): scope predicates for the reuse fast path, the deopt ledger, and
sampled verify.

The tail-blindness fingerprint fix (R5) is covered in test_runtime_fingerprint.py.
Here we test:
- scope synthesis coarseness (a band, not a point-set union),
- membership: in-scope passes, new/missing column and kind changes deopt,
- scalar slots stay fingerprint-only (empty predicate),
- the reuse-gate decision (skip vs verify vs deopt) and the deopt ledger,
- sampled verify above the size threshold recording its power.
"""
from __future__ import annotations

import types

import pandas as pd
import pytest

from semipy.agents.validator import sampled_verify_runtime_execution
from semipy.history.version_control import Slot
from semipy.kernel.guard import ScopePredicate, synthesize_scope
from semipy.kernel.operators import (
    ScopeDeoptEvent,
    append_deopt_event,
    deopt_frequency,
    get_deopt_events,
    mint_scope,
    record_scope_check,
)
from semipy.kernel.policy import counterexample_budget
from semipy.runtime_fingerprint import compute_input_profile
from semipy import slot_resolver as sr


# ---------------------------------------------------------------------------
# Scope synthesis + membership
# ---------------------------------------------------------------------------


def _frame_profile(x_range, extra_cols=None, kinds=None):
    cols = ["x"] + list(extra_cols or [])
    prof = {
        "kind": "frame",
        "columns": cols,
        "n_rows": 3,
        "n_cols": len(cols),
        "column_kinds": {c: (kinds or {}).get(c, "numeric") for c in cols},
        "column_null_rates": {c: 0.0 for c in cols},
        "column_ranges": {"x": list(x_range)},
    }
    return {"df": prof}


def test_scope_from_two_range_profiles_admits_an_input_between_them():
    # Scenario 4 (coarseness): X in [0,10] and X in [20,30] must synthesize a BAND
    # [0,30] that admits X=15 -- MDL coarsening, not an exact union of point-sets.
    scope = synthesize_scope([_frame_profile([0.0, 10.0]), _frame_profile([20.0, 30.0])])
    assert "0.0 <= df.x <= 30.0" in scope.source
    between = _frame_profile([15.0, 15.0])
    assert scope.check(between).in_scope is True
    # ...and something outside the band is still rejected.
    assert scope.check(_frame_profile([40.0, 40.0])).in_scope is False


def test_input_matching_the_evidence_profile_passes_scope():
    # Scenario 2: an input matching the minted profile is in scope.
    scope = synthesize_scope([_frame_profile([0.0, 100.0])])
    assert scope.check(_frame_profile([10.0, 20.0])).in_scope is True


def test_new_column_deopts_naming_the_violated_conjunct():
    # Scenario 3: an extra column takes the frame out of scope; the verdict names
    # the columns conjunct so the deopt event can record what was violated.
    scope = synthesize_scope([_frame_profile([0.0, 10.0])])
    check = scope.check(_frame_profile([5.0, 5.0], extra_cols=["surprise"]))
    assert check.in_scope is False
    assert check.violated_var == "df"
    assert "columns" in check.violated


def test_missing_column_deopts():
    scope = synthesize_scope([_frame_profile([0.0, 10.0], extra_cols=["y"])])
    dropped = {"df": {"kind": "frame", "columns": ["x"], "n_rows": 1, "n_cols": 1,
                      "column_kinds": {"x": "numeric"}, "column_null_rates": {"x": 0.0},
                      "column_ranges": {"x": [5.0, 5.0]}}}
    assert scope.check(dropped).in_scope is False


def test_column_kind_change_deopts():
    # A numeric column that arrives as strings violates the type-test conjunct.
    scope = synthesize_scope([_frame_profile([0.0, 10.0])])
    as_string = _frame_profile([0.0, 0.0], kinds={"x": "string"})
    as_string["df"]["column_ranges"] = {}  # strings carry no numeric range
    check = scope.check(as_string)
    assert check.in_scope is False
    assert "column_kinds" in check.violated


def test_series_length_and_range_bands():
    pa = {"s": {"kind": "series", "len": 10, "dtype_kind": "numeric", "null_rate": 0.0, "range": [0.0, 5.0]}}
    pb = {"s": {"kind": "series", "len": 20, "dtype_kind": "numeric", "null_rate": 0.0, "range": [5.0, 9.0]}}
    scope = synthesize_scope([pa, pb])
    ok = {"s": {"kind": "series", "len": 15, "dtype_kind": "numeric", "null_rate": 0.0, "range": [3.0, 7.0]}}
    assert scope.check(ok).in_scope is True
    too_long = {"s": {"kind": "series", "len": 999, "dtype_kind": "numeric", "null_rate": 0.0, "range": [3.0, 7.0]}}
    assert scope.check(too_long).in_scope is False


def test_scope_predicate_roundtrips_and_id_is_stable_and_referenceable():
    scope = synthesize_scope([_frame_profile([0.0, 10.0])])
    d = scope.to_dict()
    again = ScopePredicate.from_dict(d)
    assert again.source == scope.source
    # predicate_id is a stable hash of the source -> a later unit can reference it.
    assert again.predicate_id == scope.predicate_id
    assert d["predicate_id"] == scope.predicate_id
    assert len(scope.predicate_id) == 16


# ---------------------------------------------------------------------------
# Scalars stay fingerprint-only (scenario 6, single-particle cost preserved)
# ---------------------------------------------------------------------------


def test_scalar_only_profile_yields_an_empty_scope_predicate():
    profile = compute_input_profile({"n": 5, "label": "hello", "flag": True})
    scope = mint_scope([profile])
    assert scope.is_empty() is True
    # An empty predicate is trivially in scope: scalars never deopt on scope.
    assert scope.check(profile).in_scope is True


# ---------------------------------------------------------------------------
# The reuse-gate decision + deopt ledger (R4)
# ---------------------------------------------------------------------------


def _config(**overrides):
    base = dict(verbose=False, sampled_verify_row_threshold=10000,
                sampled_verify_epsilon=0.05, sampled_verify_delta=0.1, sampled_verify_gamma=1.0)
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _commit(commit_id="c1", fingerprint="stored-fp"):
    return types.SimpleNamespace(commit_id=commit_id, runtime_input_fingerprint=fingerprint)


def _slot_with_scope(profiles, commit_id="c1"):
    slot = Slot(slot_id="s1", call_site_info={}, function_name_base="f")
    for p in profiles:
        # profiles here are keyed-by-var dicts; seed the ledger directly.
        slot.advisor_state.setdefault("scope_profiles", []).append(p)
    sr._mint_and_store_scope(slot, commit_id)
    return slot


def test_reuse_gate_equal_fingerprint_skips_verify(monkeypatch):
    monkeypatch.setattr(sr, "save_portal", lambda *a, **k: None)
    slot = _slot_with_scope([_frame_profile([0.0, 10.0])])
    spec = types.SimpleNamespace(slot_id="s1", free_variables=["df"])
    df = pd.DataFrame({"x": [1, 2, 3]})
    skip, large = sr._reuse_scope_decision(
        slot, _commit(), spec, {"df": df},
        current_fp="fp", stored_fp="fp", portal=None, cache_dir=None,
        config=_config(), call_site=None,
    )
    assert skip is True  # equal fingerprint is the fast pre-check


def test_reuse_gate_in_scope_input_skips_verify_without_deopt(monkeypatch):
    # Scenario 2 at the gate: an in-scope, differing-fingerprint input skips verify
    # and records no deopt.
    monkeypatch.setattr(sr, "save_portal", lambda *a, **k: None)
    slot = _slot_with_scope([_frame_profile([0.0, 100.0])])
    spec = types.SimpleNamespace(slot_id="s1", free_variables=["df"])
    df = pd.DataFrame({"x": [10, 20, 30]})
    skip, large = sr._reuse_scope_decision(
        slot, _commit(), spec, {"df": df},
        current_fp="new-fp", stored_fp="stored-fp", portal=None, cache_dir=None,
        config=_config(), call_site=None,
    )
    assert skip is True
    assert get_deopt_events(slot) == []


def test_reuse_gate_out_of_scope_input_deopts_with_a_ledger_event(monkeypatch):
    # Scenario 3 end-to-end: a new column deopts, forces verify, and files a ledger
    # event naming the violated conjunct.
    monkeypatch.setattr(sr, "save_portal", lambda *a, **k: None)
    slot = _slot_with_scope([_frame_profile([0.0, 10.0])])
    spec = types.SimpleNamespace(slot_id="s1", free_variables=["df"])
    df = pd.DataFrame({"x": [1, 2, 3], "surprise": [9, 9, 9]})
    skip, large = sr._reuse_scope_decision(
        slot, _commit(), spec, {"df": df},
        current_fp="new-fp", stored_fp="stored-fp", portal=None, cache_dir=None,
        config=_config(), call_site=None,
    )
    assert skip is False  # out of scope -> never runs silently, routes to verify
    events = get_deopt_events(slot)
    assert len(events) == 1
    assert "columns" in events[0].violated_conjunct
    assert events[0].commit_id == "c1"
    assert deopt_frequency(slot) == 1.0  # 1 deopt / 1 check


def test_reuse_gate_no_minted_scope_falls_back_to_verify(monkeypatch):
    # Back-compat: a legacy commit with no minted scope behaves as today (verify).
    monkeypatch.setattr(sr, "save_portal", lambda *a, **k: None)
    slot = Slot(slot_id="s1", call_site_info={}, function_name_base="f")
    spec = types.SimpleNamespace(slot_id="s1", free_variables=["df"])
    df = pd.DataFrame({"x": [1, 2, 3]})
    skip, large = sr._reuse_scope_decision(
        slot, _commit(), spec, {"df": df},
        current_fp="new-fp", stored_fp="stored-fp", portal=None, cache_dir=None,
        config=_config(), call_site=None,
    )
    assert skip is False


def test_reuse_gate_scalar_slot_behaves_as_today(monkeypatch):
    # Scenario 6: a scalar slot's empty scope means the gate falls straight to the
    # fingerprint behavior -- equal fingerprint skips, mismatch verifies.
    monkeypatch.setattr(sr, "save_portal", lambda *a, **k: None)
    slot = _slot_with_scope([compute_input_profile({"n": 5})])
    spec = types.SimpleNamespace(slot_id="s1", free_variables=["n"])
    skip_mismatch, _ = sr._reuse_scope_decision(
        slot, _commit(), spec, {"n": 7},
        current_fp="new-fp", stored_fp="stored-fp", portal=None, cache_dir=None,
        config=_config(), call_site=None,
    )
    skip_equal, _ = sr._reuse_scope_decision(
        slot, _commit(), spec, {"n": 5},
        current_fp="fp", stored_fp="fp", portal=None, cache_dir=None,
        config=_config(), call_site=None,
    )
    assert skip_mismatch is False  # verify, exactly as today
    assert skip_equal is True      # fingerprint fast path, exactly as today


def test_reuse_gate_large_in_scope_input_routes_to_sampled_verify(monkeypatch):
    # D5: a large in-scope input does not blindly skip -- it verifies (on a sample).
    monkeypatch.setattr(sr, "save_portal", lambda *a, **k: None)
    slot = _slot_with_scope([_frame_profile([0.0, 1e9])])
    spec = types.SimpleNamespace(slot_id="s1", free_variables=["df"])
    big = pd.DataFrame({"x": range(50_000)})
    skip, large = sr._reuse_scope_decision(
        slot, _commit(), spec, {"df": big},
        current_fp="new-fp", stored_fp="stored-fp", portal=None, cache_dir=None,
        config=_config(sampled_verify_row_threshold=10000), call_site=None,
    )
    assert large is True
    assert skip is False  # large in-scope input still verifies (sampled), not skipped


def test_deopt_ledger_append_and_frequency():
    slot = Slot(slot_id="s2", call_site_info={}, function_name_base="f")
    record_scope_check(slot, True)
    record_scope_check(slot, False)
    append_deopt_event(slot, ScopeDeoptEvent(slot_id="s2", commit_id="c", violated_conjunct="df.columns == ['x']"))
    events = get_deopt_events(slot)
    assert len(events) == 1
    assert events[0].violated_conjunct == "df.columns == ['x']"
    # 2 checks recorded, 1 deopt -> frequency 0.5.
    assert deopt_frequency(slot) == 0.5


# ---------------------------------------------------------------------------
# Sampled verify (R4)
# ---------------------------------------------------------------------------


def _spec(free=("df",)):
    from semipy.types import SlotCategory

    return types.SimpleNamespace(
        expected_type=None, expected_category=SlotCategory.FUNCTION_BODY,
        output_names=[], free_variables=list(free), usage_hints=[],
    )


def test_sampled_verify_on_a_100k_frame_samples_by_epsilon_delta_and_records_power():
    # Scenario 5: a 100k-row frame is verified on the (ε, δ) sample size, and the
    # certificate records the sampling power.
    def clean(df):
        return df.dropna()

    frame = pd.DataFrame({"x": range(100_000)})
    result = sampled_verify_runtime_execution(
        fn=clean, slot_spec=_spec(), runtime_values={"df": frame},
        epsilon=0.05, delta=0.1, gamma=1.0, size_threshold=10_000,
    )
    n = counterexample_budget(0.05, 0.1, 1.0)  # reused §3.1 arithmetic
    assert result.sampled is True
    assert result.population_size == 100_000
    assert result.sample_size == n
    assert result.power == pytest.approx(1.0 - (1.0 - 0.05) ** n)
    assert 0.0 < result.power < 1.0
    assert result.validation.passed is True
    assert result.passed is True


def test_sampled_verify_below_threshold_runs_full_verify_with_power_one():
    def clean(df):
        return df.dropna()

    frame = pd.DataFrame({"x": range(100)})
    result = sampled_verify_runtime_execution(
        fn=clean, slot_spec=_spec(), runtime_values={"df": frame},
        size_threshold=10_000,
    )
    assert result.sampled is False
    assert result.power == 1.0
    assert result.passed is True


def test_sampled_verify_aggregate_sanity_flags_a_null_flood():
    # Interim element-frontier guard (origin §9.2): an implementation that floods
    # the output with nulls relative to the input fails aggregate sanity even when
    # it is type-valid.
    import numpy as np

    def wreck(df):
        return df.assign(x=np.nan)

    frame = pd.DataFrame({"x": range(100)})
    result = sampled_verify_runtime_execution(
        fn=wreck, slot_spec=_spec(), runtime_values={"df": frame},
        size_threshold=10_000,
    )
    assert result.validation.passed is True  # type-valid frame
    assert result.aggregate_ok is False      # ...but a null flood
    assert result.passed is False
