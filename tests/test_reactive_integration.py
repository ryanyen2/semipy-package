"""
Integration tests for slot-resolution + dependency graph persistence.

These tests cover the new spec-hash-based resolver behavior:
- force_regenerate=True triggers ADAPT from the head commit when commits exist
- force_regenerate=True triggers GENERATE when no commits exist
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from semipy.history import Portal, Slot, add_commit_to_slot, create_commit, freeze_constants
from semipy.reactivity import (
    DependencyGraph,
    SlotRef,
    add_dependency,
    mark_downstream_stale,
    save_dependency_graph,
    load_dependency_graph,
)
from semipy.resolver import resolve
from semipy.types import Decision, SlotCategory, SlotSpec


def _make_slot_spec(slot_id: str, spec_hash: str) -> SlotSpec:
    # For resolver unit tests we only care about slot_id/spec_hash; other fields are placeholders.
    return SlotSpec(
        slot_id=slot_id,
        source_span=("/fake/file.py", 10, 10),
        spec_text="spec",
        spec_hash=spec_hash,
        free_variables=[],
        control_context="top_level",
        expected_category=SlotCategory.EXPRESSION_STANDALONE,
        expected_type=type(None),
        output_names=[],
        formal_constraints=[],
        usage_hints=[],
        enclosing_function_source="",
        enclosing_function_qualname="test_fn",
    )


def test_resolver_force_regenerate_returns_adapt_when_slot_has_commits() -> None:
    slot_id = "slot_a"
    portal = Portal(session_id="s1", source_file="/fake/file.py", module_name="fake")
    slot = Slot(
        slot_id=slot_id,
        call_site_info={},
        function_name_base="test_fn",
        spec_hash="old",
    )
    portal.slots[slot_id] = slot

    constants_snapshot = freeze_constants({})
    commit = create_commit(
        parent_ids=(),
        generated_source="def fn():\n    return 1\n",
        template_fingerprint="fp1",
        constants_snapshot=constants_snapshot,
        prompt_snapshot="",
        decision="GENERATE",
        usage_id="",
    )
    add_commit_to_slot(slot, commit, "main", usage_id=slot_id)

    slot_spec = _make_slot_spec(slot_id, spec_hash="new")
    result = resolve(portal, slot_spec, force_regenerate=True)

    assert result.decision == Decision.ADAPT
    assert result.slot is slot
    assert result.parent_commit_ids == [commit.commit_id]


def test_resolver_force_regenerate_returns_generate_when_no_commits() -> None:
    slot_id = "slot_empty"
    portal = Portal(session_id="s2", source_file="/fake/other.py", module_name="other")
    slot = Slot(
        slot_id=slot_id,
        call_site_info={},
        function_name_base="other",
        spec_hash="old",
    )
    portal.slots[slot_id] = slot

    slot_spec = _make_slot_spec(slot_id, spec_hash="new")
    result = resolve(portal, slot_spec, force_regenerate=True)

    assert result.decision == Decision.GENERATE
    assert result.commit_id is None


def test_dependency_graph_persisted_and_loaded_with_stale_state() -> None:
    g = DependencyGraph()
    a = SlotRef("s1", "slot_a")
    b = SlotRef("s1", "slot_b")
    add_dependency(g, a, b)
    mark_downstream_stale(g, a, "upstream changed")

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        save_dependency_graph(cache_dir, g)
        path = cache_dir / "dependency_graph.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "edges" in data
        assert "statuses" in data
        loaded = load_dependency_graph(cache_dir)
        assert loaded.statuses[b.key()].stale is True
        assert loaded.statuses[b.key()].stale_reason == "upstream changed"

