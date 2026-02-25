"""
Integration tests for reactivity: resolver with force_regenerate, graph persistence in cache.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from semipy.dag import Portal, Slot, add_commit_to_slot, create_commit, freeze_constants
from semipy.reactive import (
    DependencyGraph,
    SlotRef,
    add_dependency,
    load_dependency_graph,
    mark_downstream_stale,
    save_dependency_graph,
)
from semipy.resolver import resolve
from semipy.types import PromptTemplate, SemiCallSite, TemplatePart, Usage


def _make_usage(site_id: str, fingerprint: str = "fp1", constants: dict[str, Any] | None = None) -> Usage:
    call_site = SemiCallSite(filename="/fake/file.py", lineno=10, func_qualname="test_fn")
    template = PromptTemplate(
        template_parts=[TemplatePart(is_literal=True, value="x")],
        variable_names=[],
        variable_expressions=[],
    )
    return Usage(call_site=call_site, template=template, constant_values=constants or {})


def test_resolver_force_regenerate_returns_adapt_when_branch_exists() -> None:
    call_site = SemiCallSite(filename="/fake/file.py", lineno=10, func_qualname="test_fn")
    slot_id = call_site.site_id
    portal = Portal(session_id="s1", source_file="/fake/file.py", module_name="fake")
    slot = Slot(
        slot_id=slot_id,
        call_site_info={"filename": call_site.filename, "lineno": call_site.lineno, "func_qualname": call_site.func_qualname},
        function_name_base="test_fn",
    )
    constants_snapshot = freeze_constants({})
    commit = create_commit(
        parent_ids=(),
        generated_source="def fn(): return 1",
        template_fingerprint="fp1",
        constants_snapshot=constants_snapshot,
        prompt_snapshot="",
        decision="GENERATE",
        usage_id="",
    )
    add_commit_to_slot(slot, commit, "main", usage_id="u1")
    portal.slots[slot_id] = slot

    usage = _make_usage(slot_id, "fp1", {})
    result = resolve(portal, usage, "fp1", {}, force_regenerate=True)
    assert result.decision.value == "adapt"
    assert result.slot is slot
    assert result.parent_commit_ids == [commit.commit_id]


def test_resolver_force_regenerate_returns_generate_when_no_branch() -> None:
    call_site = SemiCallSite(filename="/fake/other.py", lineno=1, func_qualname="other")
    slot_id = call_site.site_id
    portal = Portal(session_id="s2", source_file="/fake/other.py", module_name="other")
    slot = Slot(
        slot_id=slot_id,
        call_site_info={},
        function_name_base="other",
    )
    portal.slots[slot_id] = slot
    usage = _make_usage(slot_id, "fp_other", {})
    result = resolve(portal, usage, "fp_other", {}, force_regenerate=True)
    assert result.decision.value == "generate"
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
