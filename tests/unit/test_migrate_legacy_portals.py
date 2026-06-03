from __future__ import annotations

import ast

from semipy.history.version_control import (
    Portal,
    Slot,
    add_commit_to_slot,
    create_commit,
    freeze_constants,
)
from semipy.store import _portal_path, load_portal, migrate_legacy_portals, save_portal


def _legacy_portal(cache_dir, session_id, name, slot_id):
    p = Portal(session_id=session_id, source_file=f"{name}.py", module_name=name)
    slot = Slot(
        slot_id=slot_id,
        call_site_info={"filename": f"/proj/{name}.py", "lineno": 1, "func_qualname": name},
        function_name_base=name,
        slot_spec={"spec_text": f"do {name}", "source_span": [f"/proj/{name}.py", 1, 2]},
    )
    c = create_commit((), f"def {name}(x):\n    return x\n", "h", freeze_constants({}), "s", "GENERATE", usage_id=slot_id)
    add_commit_to_slot(slot, c, "main", slot_id)
    p.slots[slot_id] = slot
    save_portal(cache_dir, p)
    return c


def test_merge_disjoint_legacy_portals(tmp_path):
    cache = tmp_path / ".semiformal"
    cache.mkdir()
    _legacy_portal(cache, "leg1aaaaaaaaaaaa", "foo", "slot_foo")
    _legacy_portal(cache, "leg2bbbbbbbbbbbb", "bar", "slot_bar")

    project_sid = "proj0000000000aa"
    merged = migrate_legacy_portals(cache, project_sid, "/proj", "proj")
    assert merged is not None
    assert sorted(merged.slots) == ["slot_bar", "slot_foo"]

    # The merged project portal is persisted and its dispatch module is valid Python
    # containing both functions.
    assert _portal_path(cache, project_sid).exists()
    dispatch = (cache / "runtime" / "proj.semi.py").read_text(encoding="utf-8")
    ast.parse(dispatch)
    assert "def foo_" in dispatch and "def bar_" in dispatch


def test_migration_is_idempotent(tmp_path):
    cache = tmp_path / ".semiformal"
    cache.mkdir()
    _legacy_portal(cache, "leg1aaaaaaaaaaaa", "foo", "slot_foo")
    project_sid = "proj0000000000aa"
    assert migrate_legacy_portals(cache, project_sid, "/proj", "proj") is not None
    # Project portal now exists -> second call is a no-op.
    assert migrate_legacy_portals(cache, project_sid, "/proj", "proj") is None


def test_overlapping_slot_id_unions_commits(tmp_path):
    cache = tmp_path / ".semiformal"
    cache.mkdir()
    c1 = _legacy_portal(cache, "leg1aaaaaaaaaaaa", "foo", "slot_shared")
    # Second legacy portal reuses the same slot_id but adds another commit.
    p2 = load_portal(cache, "leg2bbbbbbbbbbbb", "foo2.py", "foo2")
    slot = Slot(slot_id="slot_shared", call_site_info={}, function_name_base="foo")
    c2 = create_commit((c1.commit_id,), "def foo(x):\n    return x + 1\n", "h2", freeze_constants({}), "s", "ADAPT", usage_id="slot_shared")
    add_commit_to_slot(slot, c2, "main", "slot_shared")
    p2.slots["slot_shared"] = slot
    save_portal(cache, p2)

    merged = migrate_legacy_portals(cache, "proj0000000000aa", "/proj", "proj")
    assert merged is not None
    # The shared slot retains both commits (content-addressed union).
    assert set(merged.slots["slot_shared"].commits) == {c1.commit_id, c2.commit_id}


def test_no_legacy_portals_returns_none(tmp_path):
    cache = tmp_path / ".semiformal"
    cache.mkdir()
    assert migrate_legacy_portals(cache, "proj0000000000aa", "/proj", "proj") is None


def test_merge_preserves_contract_and_ledger_on_collision(tmp_path):
    """A slot_id collision must not drop the behavioral contract / effect ledger."""
    from semipy.store import _merge_slot

    base = Slot(slot_id="s", call_site_info={}, function_name_base="f")
    c1 = create_commit((), "def f(x):\n    return x\n", "h", freeze_constants({}), "s", "GENERATE", usage_id="s")
    add_commit_to_slot(base, c1, "main", "s")  # richer (1 commit)

    fold = Slot(slot_id="s", call_site_info={}, function_name_base="f")
    fold.contract = {"version": 2, "cases": {"case1": {"kind": "invariant"}}}
    fold.ledger = {"events": {"e1": {"status": "applied"}}}

    merged = _merge_slot(base, fold)
    assert merged.contract == fold.contract  # carried over (base had none)
    assert merged.ledger == fold.ledger
    # The richer slot's main head is not clobbered by the poorer slot.
    assert merged.branches["main"].head == c1.commit_id
