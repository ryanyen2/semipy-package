"""U9: resolve by picking a branch (LLM-free head swap)."""
from __future__ import annotations

import pytest

from semipy.decisions.model import Branch, Decision, DecisionSet
from semipy.decisions.persistence import decision_set_for
from semipy.decisions.resolve import DecisionResolveError, pick_branch
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
    d = Decision(
        germ="null",
        axis_label="null cover",
        guard="cover is None",
        branches=[Branch("skip", ["A"], 0.6), Branch("count as 0", ["B"], 0.4)],
    )
    return DecisionSet(slot_id="slot1", decisions=[d], candidates={"A": _SKIP, "B": _ZERO})


def test_pick_swaps_head_to_stored_candidate():
    slot = _slot_with_head()
    dset = _decision_set()
    did = dset.decisions[0].decision_id
    res = pick_branch(slot, dset, decision_id=did, fate_label="skip", usage_id="u1")
    assert res.source == _SKIP
    assert most_recent_branch_head(slot).generated_source == _SKIP
    assert res.commit_id == most_recent_branch_head(slot).commit_id


def test_pick_marks_decision_resolved_and_writes_spec_clause():
    slot = _slot_with_head()
    dset = _decision_set()
    did = dset.decisions[0].decision_id
    res = pick_branch(slot, dset, decision_id=did, fate_label="skip")
    assert dset.decisions[0].status == "resolved"
    assert dset.decisions[0].resolution["via"] == "pick"
    assert "skip" in res.spec_clause
    assert "cover is None" in res.spec_clause


def test_pick_preserves_slot_id():
    slot = _slot_with_head()
    before = slot.slot_id
    dset = _decision_set()
    pick_branch(slot, dset, decision_id=dset.decisions[0].decision_id, fate_label="count as 0")
    assert slot.slot_id == before


def test_pick_persists_resolution_on_slot():
    slot = _slot_with_head()
    dset = _decision_set()
    pick_branch(slot, dset, decision_id=dset.decisions[0].decision_id, fate_label="skip")
    reloaded = decision_set_for(slot)
    assert reloaded.decisions[0].status == "resolved"


def test_pick_with_missing_candidate_fails_loudly():
    slot = _slot_with_head()
    d = Decision(germ="null", axis_label="x", branches=[Branch("gone", ["MISSING"], 1.0)])
    dset = DecisionSet(slot_id="slot1", decisions=[d], candidates={})
    with pytest.raises(DecisionResolveError):
        pick_branch(slot, dset, decision_id=d.decision_id, fate_label="gone")


def test_pick_does_not_lose_prior_commits():
    slot = _slot_with_head()
    dset = _decision_set()
    pick_branch(slot, dset, decision_id=dset.decisions[0].decision_id, fate_label="skip")
    # Original commit still in history; pick added a new head, did not delete.
    assert len(slot.commits) == 2
