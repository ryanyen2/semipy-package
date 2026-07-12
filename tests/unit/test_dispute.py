"""U5: `semipy dispute` -- record a disputed property as a contract case even
when the slot has no surfaced fork to assert against.

Mirrors ``tests/test_decision_cli.py``'s CLI-level pattern: build a portal on
disk, drive the CLI handler, assert the portal mutated correctly.
"""
from __future__ import annotations

from pathlib import Path

from semipy.cli import cmd_dispute
from semipy.decisions.model import Branch, Decision, DecisionSet
from semipy.decisions.persistence import attach_decision_set
from semipy.history.version_control import Portal, Slot
from semipy.store import _portal_path, load_portal, save_portal

_PROPERTY = "a site with all-null readings is omitted from the output"


def _portal_with_slot(tmp: Path, slot: Slot) -> tuple[Path, str]:
    session_id = "sess1"
    slot_id = slot.slot_id
    portal = Portal(session_id=session_id, source_file="m.py", module_name="m", slots={slot_id: slot})
    save_portal(tmp, portal)
    return _portal_path(tmp, session_id), slot_id


def test_dispute_on_slot_with_no_decision_records_case_and_signals_regen(tmp_path, capsys):
    """The common case: a slot that never surfaced a fork still accepts a dispute."""
    slot = Slot(slot_id="t.avg", call_site_info={}, function_name_base="avg")
    portal_path, slot_id = _portal_with_slot(tmp_path, slot)

    cmd_dispute(portal_path, slot_id, _PROPERTY, as_json=False)

    reloaded = load_portal(tmp_path, "sess1", "m.py", "m")
    reloaded_slot = reloaded.slots[slot_id]
    asserted = reloaded_slot.contract.get("asserted_properties", [])
    assert any(a.get("property") == _PROPERTY for a in asserted)
    # No candidate exists to satisfy it, so it always regenerates against it.
    decisions = reloaded_slot.decision_set["decisions"]
    assert len(decisions) == 1 and decisions[0]["status"] == "open"
    assert "recorded property" in capsys.readouterr().out


def test_dispute_on_slot_with_open_decision_reuses_it_instead_of_synthesizing(tmp_path):
    """A slot that already has a surfaced fork disputes through that fork, not a
    second, redundant placeholder decision."""
    slot = Slot(slot_id="t.split", call_site_info={}, function_name_base="split")
    decision = Decision(
        germ="output", axis_label="null handling",
        branches=[Branch(fate_label="skip", candidate_ids=["c0"], weight=1.0, signature=["a"])],
    )
    dset = DecisionSet(slot_id=slot.slot_id, decisions=[decision], candidates={"c0": "def f(): return 1\n"})
    attach_decision_set(slot, dset)
    portal_path, slot_id = _portal_with_slot(tmp_path, slot)

    cmd_dispute(portal_path, slot_id, _PROPERTY, as_json=False)

    reloaded = load_portal(tmp_path, "sess1", "m.py", "m")
    reloaded_slot = reloaded.slots[slot_id]
    decisions = reloaded_slot.decision_set["decisions"]
    # Reused the existing decision -- still exactly one, matching its decision_id.
    assert len(decisions) == 1
    assert decisions[0]["decision_id"] == decision.decision_id
    asserted = reloaded_slot.contract.get("asserted_properties", [])
    assert any(a.get("decision_id") == decision.decision_id for a in asserted)


def test_dispute_is_idempotent_on_case_recording(tmp_path):
    slot = Slot(slot_id="t.norm", call_site_info={}, function_name_base="norm")
    portal_path, slot_id = _portal_with_slot(tmp_path, slot)

    cmd_dispute(portal_path, slot_id, _PROPERTY, as_json=False)
    cmd_dispute(portal_path, slot_id, _PROPERTY, as_json=False)

    reloaded = load_portal(tmp_path, "sess1", "m.py", "m")
    asserted = reloaded.slots[slot_id].contract.get("asserted_properties", [])
    assert len(asserted) == 1


def test_dispute_unknown_slot_errors(tmp_path):
    slot = Slot(slot_id="t.real", call_site_info={}, function_name_base="real")
    portal_path, _ = _portal_with_slot(tmp_path, slot)
    try:
        cmd_dispute(portal_path, "does-not-exist", _PROPERTY, as_json=False)
    except SystemExit:
        pass
    else:
        raise AssertionError("expected SystemExit on unknown slot_id")
