"""U7: DecisionSet model and portal persistence."""
from __future__ import annotations

from semipy.decisions.divergence import observe_pure
from semipy.decisions.model import Branch, Decision, DecisionSet
from semipy.decisions.persistence import attach_decision_set, decision_set_for
from semipy.orchestration.roles.decision_classifier import classify_divergence
from semipy.history.version_control import Slot
from semipy.store import _slot_from_dict, _slot_to_dict

_SKIP = "def avg(rows):\n    v=[r['c'] for r in rows if r['c'] is not None]\n    return round(sum(v)/len(v),4) if v else None\n"
_ZERO = "def avg(rows):\n    v=[(r['c'] or 0) for r in rows]\n    return round(sum(v)/len(v),4) if v else None\n"
_ROWS = [{"rows": [{"c": 0.4}, {"c": None}, {"c": 0.7}]}]


def _make_set():
    div = observe_pure({"A": _SKIP, "B": _ZERO}, free_variables=["rows"], sample_rows=_ROWS)
    decisions = classify_divergence(div, germ="null", use_llm=False)
    return DecisionSet(slot_id="slot1", decisions=decisions, candidates={"A": _SKIP, "B": _ZERO})


def _empty_slot() -> Slot:
    return Slot(slot_id="slot1", call_site_info={}, function_name_base="avg")


def test_decision_set_round_trips_through_dict():
    dset = _make_set()
    restored = DecisionSet.from_dict(dset.to_dict())
    assert restored.slot_id == "slot1"
    assert len(restored.decisions) == 1
    assert len(restored.decisions[0].branches) == 2
    assert set(restored.candidates) == {"A", "B"}


def test_round_trips_through_portal_slot_serialization():
    dset = _make_set()
    slot = _empty_slot()
    attach_decision_set(slot, dset)
    # Through the store's slot serialization (what lands in .portal.json).
    reloaded = _slot_from_dict(_slot_to_dict(slot))
    out = decision_set_for(reloaded)
    assert out is not None
    assert out.decisions[0].germ == "null"


def test_losing_candidate_sources_are_retrievable():
    dset = _make_set()
    slot = _empty_slot()
    attach_decision_set(slot, dset)
    reloaded = decision_set_for(_slot_from_dict(_slot_to_dict(slot)))
    # Both winning and losing candidate sources survive, keyed by id.
    assert reloaded.candidates["A"] == _SKIP
    assert reloaded.candidates["B"] == _ZERO


def test_unambiguous_slot_persists_no_decision_set():
    empty = DecisionSet(slot_id="slot1", decisions=[], candidates={})
    slot = _empty_slot()
    attach_decision_set(slot, empty)
    assert slot.decision_set == {}
    assert decision_set_for(_slot_from_dict(_slot_to_dict(slot))) is None


def test_legacy_slot_without_decision_set_loads():
    # A slot dict that predates the field (no 'decision_set' key) still loads.
    slot = _empty_slot()
    raw = _slot_to_dict(slot)
    raw.pop("decision_set", None)
    reloaded = _slot_from_dict(raw)
    assert reloaded.decision_set == {}


def test_resolution_state_survives_round_trip():
    d = Decision(germ="null", axis_label="null reading", branches=[Branch("skip", ["A"], 0.5)])
    d.status = "resolved"
    d.resolution = {"via": "pick", "branch": "skip"}
    dset = DecisionSet(slot_id="s", decisions=[d], candidates={"A": _SKIP})
    restored = DecisionSet.from_dict(dset.to_dict())
    assert restored.decisions[0].status == "resolved"
    assert restored.decisions[0].resolution == {"via": "pick", "branch": "skip"}
