"""U10: resolve by asserting a property."""
from __future__ import annotations

from semipy.decisions.model import Branch, Decision, DecisionSet
from semipy.decisions.resolve import assert_property
from semipy.history.version_control import (
    Slot,
    add_commit_to_slot,
    create_commit,
    most_recent_branch_head,
)

_SKIP = "def avg(rows):\n    return 'skip-impl'\n"
_ZERO = "def avg(rows):\n    return 'zero-impl'\n"


def _slot_with_head() -> Slot:
    slot = Slot(slot_id="slot1", call_site_info={}, function_name_base="avg")
    commit = create_commit(
        parent_ids=(),
        generated_source="def avg(rows):\n    return 'original'\n",
        template_fingerprint="tfp1",
        constants_snapshot=(),
        prompt_snapshot="p",
        decision="GENERATE",
        usage_id="u1",
    )
    add_commit_to_slot(slot, commit, branch_name="main", usage_id="u1")
    return slot


def _decision_set() -> DecisionSet:
    d = Decision(germ="null", axis_label="null cover", branches=[Branch("skip", ["A"], 0.6), Branch("zero", ["B"], 0.4)])
    return DecisionSet(slot_id="slot1", decisions=[d], candidates={"A": _SKIP, "B": _ZERO})


_PROP = "a fully-null site must not change other sites' averages"


def test_assert_filters_to_satisfying_candidate_and_commits():
    slot = _slot_with_head()
    dset = _decision_set()
    did = dset.decisions[0].decision_id
    # Only candidate A satisfies the property.
    res = assert_property(
        slot, dset, decision_id=did, property_text=_PROP, satisfies=lambda cid: cid == "A"
    )
    assert res.satisfying_candidate_ids == ["A"]
    assert not res.regen_needed
    assert most_recent_branch_head(slot).generated_source == _SKIP
    assert dset.decisions[0].status == "resolved"


def test_assert_records_contract_case():
    slot = _slot_with_head()
    dset = _decision_set()
    did = dset.decisions[0].decision_id
    assert_property(slot, dset, decision_id=did, property_text=_PROP, satisfies=lambda cid: True)
    cases = slot.contract["asserted_properties"]
    assert any(c["property"] == _PROP for c in cases)


def test_assert_signals_regen_when_no_candidate_satisfies():
    slot = _slot_with_head()
    dset = _decision_set()
    did = dset.decisions[0].decision_id
    res = assert_property(
        slot, dset, decision_id=did, property_text=_PROP, satisfies=lambda cid: False
    )
    assert res.regen_needed
    assert res.commit_id is None
    # Decision stays open until a satisfying impl exists; case still recorded.
    assert dset.decisions[0].status == "open"
    assert dset.decisions[0].resolution["regen_needed"] is True


def test_assert_is_idempotent_on_case_recording():
    slot = _slot_with_head()
    dset = _decision_set()
    did = dset.decisions[0].decision_id
    assert_property(slot, dset, decision_id=did, property_text=_PROP, satisfies=lambda cid: False)
    assert_property(slot, dset, decision_id=did, property_text=_PROP, satisfies=lambda cid: False)
    assert len(slot.contract["asserted_properties"]) == 1
