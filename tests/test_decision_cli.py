"""Phase 3: CLI bridge for steering -- pick-decision / assert-decision.

Offline: builds a portal on disk with a surfaced DecisionSet (two candidate
sources), then drives the CLI handlers and asserts the portal mutated correctly.
"""
from __future__ import annotations

from pathlib import Path

from semipy.cli import cmd_assert_decision, cmd_pick_decision
from semipy.decisions.model import Branch, Decision, DecisionSet
from semipy.decisions.persistence import attach_decision_set
from semipy.history.version_control import Portal, Slot
from semipy.store import _portal_path, load_portal, save_portal

_KEEP = "def split_name(name):\n    p = name.split()\n    return {'first_name': p[0], 'last_name': ' '.join(p[1:])}\n"
_LAST = "def split_name(name):\n    p = name.split()\n    return {'first_name': p[0], 'last_name': p[-1]}\n"


def _portal_with_decision(tmp: Path) -> tuple[Path, str, Decision]:
    session_id = "sess1"
    slot_id = "t.split"
    slot = Slot(slot_id=slot_id, call_site_info={}, function_name_base="split_name")
    decision = Decision(
        germ="output",
        axis_label="multi-part last name",
        branches=[
            Branch(fate_label="keep all remaining", candidate_ids=["c0"], weight=0.6, signature=["a"]),
            Branch(fate_label="last word only", candidate_ids=["c1"], weight=0.4, signature=["b"]),
        ],
        guard="name has more than two words",
    )
    dset = DecisionSet(slot_id=slot_id, decisions=[decision], candidates={"c0": _KEEP, "c1": _LAST})
    attach_decision_set(slot, dset)
    portal = Portal(session_id=session_id, source_file="m.py", module_name="m", slots={slot_id: slot})
    save_portal(tmp, portal)
    return _portal_path(tmp, session_id), slot_id, decision


def test_pick_decision_swaps_head_and_resolves(tmp_path, capsys):
    portal_path, slot_id, decision = _portal_with_decision(tmp_path)
    cmd_pick_decision(portal_path, slot_id, decision.decision_id, "last word only", as_json=False)

    reloaded = load_portal(tmp_path, "sess1", "m.py", "m")
    slot = reloaded.slots[slot_id]
    # The picked candidate's source is now the head implementation.
    from semipy.history.version_control import most_recent_branch_head

    head = most_recent_branch_head(slot)
    assert head is not None and head.generated_source == _LAST
    # The decision is recorded resolved via pick.
    raw = slot.decision_set["decisions"][0]
    assert raw["status"] == "resolved"
    assert raw["resolution"]["via"] == "pick"
    assert "last word only" in capsys.readouterr().out


def test_assert_decision_records_contract_case(tmp_path, capsys):
    portal_path, slot_id, decision = _portal_with_decision(tmp_path)
    cmd_assert_decision(
        portal_path, slot_id, decision.decision_id,
        "a site with all-null covers is omitted", as_json=False,
    )
    reloaded = load_portal(tmp_path, "sess1", "m.py", "m")
    slot = reloaded.slots[slot_id]
    asserted = slot.contract.get("asserted_properties", [])
    assert any("all-null" in a.get("property", "") for a in asserted)
    assert "recorded property" in capsys.readouterr().out


def test_pick_unknown_fate_errors(tmp_path):
    portal_path, slot_id, decision = _portal_with_decision(tmp_path)
    try:
        cmd_pick_decision(portal_path, slot_id, decision.decision_id, "nonexistent", as_json=False)
    except SystemExit:
        pass
    else:
        raise AssertionError("expected SystemExit on unknown fate")
