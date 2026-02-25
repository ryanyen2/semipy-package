"""
Tests for the reactive dependency graph: edges, cycle detection, cascade, persistence.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from semipy.reactivity import (
    DependencyGraph,
    SlotRef,
    add_dependency,
    add_downstream_requirement,
    clear_stale,
    get_downstream_requirements,
    get_transitive_downstream,
    get_transitive_upstream,
    is_stale,
    load_dependency_graph,
    mark_downstream_stale,
    save_dependency_graph,
    update_slot_commit,
)


def test_slot_ref_key() -> None:
    r = SlotRef(session_id="s1", slot_id="slot_a")
    assert r.key() == "s1:slot_a"


def test_add_dependency_creates_edge_and_statuses() -> None:
    g = DependencyGraph()
    a = SlotRef("s1", "a")
    b = SlotRef("s1", "b")
    assert add_dependency(g, a, b) is True
    assert len(g.edges) == 1
    assert g.forward_adj.get(a.key()) == {b.key()}
    assert g.backward_adj.get(b.key()) == {a.key()}
    assert a.key() in g.statuses
    assert b.key() in g.statuses


def test_add_dependency_rejects_self_cycle() -> None:
    g = DependencyGraph()
    a = SlotRef("s1", "a")
    assert add_dependency(g, a, a) is False
    assert len(g.edges) == 0


def test_add_dependency_rejects_cycle() -> None:
    g = DependencyGraph()
    a, b, c = SlotRef("s1", "a"), SlotRef("s1", "b"), SlotRef("s1", "c")
    add_dependency(g, a, b)
    add_dependency(g, b, c)
    assert add_dependency(g, c, a) is False
    assert len(g.edges) == 2


def test_get_transitive_downstream() -> None:
    g = DependencyGraph()
    a, b, c = SlotRef("s1", "a"), SlotRef("s1", "b"), SlotRef("s1", "c")
    add_dependency(g, a, b)
    add_dependency(g, b, c)
    down = get_transitive_downstream(g, a)
    assert b.key() in down
    assert c.key() in down
    assert a.key() not in down


def test_get_transitive_upstream() -> None:
    g = DependencyGraph()
    a, b, c = SlotRef("s1", "a"), SlotRef("s1", "b"), SlotRef("s1", "c")
    add_dependency(g, a, b)
    add_dependency(g, b, c)
    up = get_transitive_upstream(g, c)
    assert a.key() in up
    assert b.key() in up
    assert c.key() not in up


def test_mark_downstream_stale_cascades() -> None:
    g = DependencyGraph()
    a, b, c = SlotRef("s1", "a"), SlotRef("s1", "b"), SlotRef("s1", "c")
    add_dependency(g, a, b)
    add_dependency(g, b, c)
    n = mark_downstream_stale(g, a, "upstream changed")
    assert n == 2
    assert is_stale(g, b) is True
    assert is_stale(g, c) is True
    assert is_stale(g, a) is False


def test_clear_stale() -> None:
    g = DependencyGraph()
    a = SlotRef("s1", "a")
    add_dependency(g, a, SlotRef("s1", "b"))
    mark_downstream_stale(g, a, "reason")
    clear_stale(g, SlotRef("s1", "b"))
    assert is_stale(g, SlotRef("s1", "b")) is False


def test_update_slot_commit_and_downstream_requirements() -> None:
    g = DependencyGraph()
    a = SlotRef("s1", "a")
    add_dependency(g, a, SlotRef("s1", "b"))
    update_slot_commit(g, a, "commit_xyz")
    assert g.statuses[a.key()].current_commit_id == "commit_xyz"
    add_downstream_requirement(g, a, "required_columns", ["x", "y"])
    reqs = get_downstream_requirements(g, a)
    assert reqs.get("required_columns") == ["x", "y"]


def test_persistence_roundtrip() -> None:
    g = DependencyGraph()
    a, b = SlotRef("s1", "a"), SlotRef("s1", "b")
    add_dependency(g, a, b)
    update_slot_commit(g, a, "c1")
    mark_downstream_stale(g, a, "test reason")
    add_downstream_requirement(g, a, "required_columns", ["col1"])
    with tempfile.TemporaryDirectory() as d:
        cache_dir = Path(d)
        save_dependency_graph(cache_dir, g)
        loaded = load_dependency_graph(cache_dir)
    assert len(loaded.edges) == 1
    assert loaded.forward_adj.get(a.key()) == {b.key()}
    assert loaded.statuses[b.key()].stale is True
    assert loaded.statuses[b.key()].stale_reason == "test reason"
    assert loaded.statuses[a.key()].current_commit_id == "c1"
    assert get_downstream_requirements(loaded, a) == {"required_columns": ["col1"]}
