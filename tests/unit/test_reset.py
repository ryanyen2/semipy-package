from __future__ import annotations

import json
import subprocess
import sys

import pytest

from semipy.history.version_control import (
    Portal,
    Slot,
    add_commit_to_slot,
    create_commit,
    freeze_constants,
)
from semipy.history.version_lock import (
    LOCK_REF_KEY,
    lock_slot_to_commit,
    reset_slot,
    reset_version,
)
from semipy.store import _portal_path, save_portal


def _slot_with_two_commits():
    slot = Slot(
        slot_id="slotA",
        call_site_info={"filename": "/proj/a.py", "lineno": 12, "func_qualname": "f"},
        function_name_base="f",
        slot_spec={"spec_text": "extract domain", "source_span": ["/proj/a.py", 12, 14]},
    )
    c1 = create_commit((), "def f(u):\n    return u\n", "h1", freeze_constants({}), "s", "GENERATE", usage_id="slotA")
    add_commit_to_slot(slot, c1, "main", "slotA")
    c2 = create_commit((c1.commit_id,), "def f(u):\n    return u.upper()\n", "h2", freeze_constants({}), "s", "ADAPT", usage_id="slotA")
    # Make c2 strictly newer.
    object.__setattr__(c2, "timestamp", c1.timestamp + 1.0)
    add_commit_to_slot(slot, c2, "main", "slotA")
    return slot, c1, c2


def _portal_with_slot(slot):
    p = Portal(session_id="sess", source_file="/proj", module_name="proj")
    p.slots[slot.slot_id] = slot
    p.spec_map[slot.slot_id] = "f:12-14"
    p.enclosing_function_slots["f"] = [slot.slot_id]
    return p


def test_reset_slot_removes_slot_and_metadata():
    slot, _, _ = _slot_with_two_commits()
    portal = _portal_with_slot(slot)
    reset_slot(portal, "slotA")
    assert "slotA" not in portal.slots
    assert "slotA" not in portal.spec_map
    assert "f" not in portal.enclosing_function_slots


def test_reset_slot_unknown_raises():
    portal = Portal(session_id="s", source_file="/p", module_name="p")
    with pytest.raises(KeyError):
        reset_slot(portal, "nope")


def test_reset_version_falls_back_to_parent():
    slot, c1, c2 = _slot_with_two_commits()
    portal = _portal_with_slot(slot)
    reset_version(portal, "slotA", c2.commit_id)
    s = portal.slots["slotA"]
    assert set(s.commits) == {c1.commit_id}
    # The default branch head falls back to the surviving parent commit.
    assert s.branches[s.default_branch].head == c1.commit_id


def test_reset_version_last_commit_empties_slot():
    slot, c1, _ = _slot_with_two_commits()
    portal = _portal_with_slot(slot)
    reset_version(portal, "slotA", slot.branches["main"].head)  # remove head (c2)
    reset_version(portal, "slotA", c1.commit_id)  # remove the last one
    s = portal.slots["slotA"]
    assert s.commits == {}
    assert s.default_branch not in s.branches


def test_reset_version_clears_lock_ref():
    slot, c1, c2 = _slot_with_two_commits()
    portal = _portal_with_slot(slot)
    lock_slot_to_commit(portal, "slotA", c2.commit_id)
    assert portal.slots["slotA"].refs.get(LOCK_REF_KEY) == c2.commit_id
    reset_version(portal, "slotA", c2.commit_id)
    assert LOCK_REF_KEY not in portal.slots["slotA"].refs


def _run_cli(*args):
    r = subprocess.run([sys.executable, "-m", "semipy", *args], capture_output=True, text=True)
    return r.stdout + r.stderr, r.returncode


def test_cli_slots_reset_version_reset_slot(tmp_path):
    cache = tmp_path / ".semiformal"
    cache.mkdir()
    slot, c1, c2 = _slot_with_two_commits()
    portal = _portal_with_slot(slot)
    save_portal(cache, portal)
    pp = str(_portal_path(cache, "sess"))

    out, rc = _run_cli("slots", "--portal", pp, "--json")
    assert rc == 0
    rows = json.loads(out)
    assert rows[0]["versions"] == 2 and rows[0]["decision"] == "ADAPT"

    out, rc = _run_cli("reset-version", "--portal", pp, "--slot-id", "slotA", "--commit-id", c2.commit_id)
    assert rc == 0
    rows = json.loads(_run_cli("slots", "--portal", pp, "--json")[0])
    assert rows[0]["versions"] == 1 and rows[0]["decision"] == "GENERATE"

    out, rc = _run_cli("reset-slot", "--portal", pp, "--slot-id", "slotA")
    assert rc == 0
    assert json.loads(_run_cli("slots", "--portal", pp, "--json")[0]) == []


def test_cli_reset_slot_unknown_exits_nonzero(tmp_path):
    cache = tmp_path / ".semiformal"
    cache.mkdir()
    save_portal(cache, Portal(session_id="sess", source_file="/p", module_name="p"))
    pp = str(_portal_path(cache, "sess"))
    _, rc = _run_cli("reset-slot", "--portal", pp, "--slot-id", "nope")
    assert rc != 0
