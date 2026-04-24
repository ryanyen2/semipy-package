"""Tests for US-002 reactivity wiring: commit-change propagation and staleness."""
from __future__ import annotations

from semipy.reactivity.reactive import (
    DependencyGraph,
    SlotRef,
    add_dependency,
    is_stale,
    update_slot_commit,
)


def _ref(slot_id: str, session_id: str = "s1") -> SlotRef:
    return SlotRef(session_id=session_id, slot_id=slot_id)


def test_first_commit_does_not_mark_stale() -> None:
    g = DependencyGraph()
    a, b = _ref("a"), _ref("b")
    add_dependency(g, upstream=a, downstream=b)
    prior = update_slot_commit(g, a, "commit_A1")
    assert prior == ""
    assert is_stale(g, b) is False


def test_upstream_commit_change_marks_downstream_stale() -> None:
    g = DependencyGraph()
    a, b = _ref("a"), _ref("b")
    add_dependency(g, upstream=a, downstream=b)
    update_slot_commit(g, a, "commit_A1")
    update_slot_commit(g, b, "commit_B1")
    assert is_stale(g, b) is False

    prior = update_slot_commit(g, a, "commit_A2")
    assert prior == "commit_A1"
    assert is_stale(g, b) is True, "b must be stale after upstream commit change"


def test_transitive_downstream_marked_stale() -> None:
    g = DependencyGraph()
    a, b, c = _ref("a"), _ref("b"), _ref("c")
    add_dependency(g, upstream=a, downstream=b)
    add_dependency(g, upstream=b, downstream=c)
    update_slot_commit(g, a, "A1")
    update_slot_commit(g, b, "B1")
    update_slot_commit(g, c, "C1")

    update_slot_commit(g, a, "A2")
    assert is_stale(g, b) is True
    assert is_stale(g, c) is True, "transitive downstream must be stale"


def test_same_commit_does_not_mark_stale() -> None:
    g = DependencyGraph()
    a, b = _ref("a"), _ref("b")
    add_dependency(g, upstream=a, downstream=b)
    update_slot_commit(g, a, "A1")
    update_slot_commit(g, b, "B1")

    # Re-recording the same commit id must not flip downstream to stale.
    update_slot_commit(g, a, "A1")
    assert is_stale(g, b) is False


def test_stale_reason_records_short_ids() -> None:
    g = DependencyGraph()
    a, b = _ref("a"), _ref("b")
    add_dependency(g, upstream=a, downstream=b)
    update_slot_commit(g, a, "aaaaaaaa_old_commit_id")
    update_slot_commit(g, a, "bbbbbbbb_new_commit_id")
    status = g.statuses[b.key()]
    assert status.stale is True
    assert "aaaaaaaa" in status.stale_reason
    assert "bbbbbbbb" in status.stale_reason
