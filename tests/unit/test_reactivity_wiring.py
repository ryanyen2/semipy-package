"""Tests for US-002 reactivity wiring: commit-change propagation and staleness."""
from __future__ import annotations

from semipy.reactivity.flow import attach_producer_flow, create_flow, extract_flow
from semipy.reactivity.reactive import (
    DependencyGraph,
    SlotRef,
    add_dependency,
    is_stale,
    record_consumed,
    remove_dependency,
    set_incoming_edges,
    stale_against_inputs,
    update_slot_commit,
)


def _r(slot_id: str) -> SlotRef:
    return SlotRef(session_id="s", slot_id=slot_id)


def test_set_incoming_edges_prunes_dropped_dependency() -> None:
    # A->C wired once; later C is observed taking only B -> the A->C ghost edge is pruned.
    g = DependencyGraph()
    A, B, C = _r("A"), _r("B"), _r("C")
    add_dependency(g, upstream=A, downstream=C)
    assert "s:A" in g.backward_adj["s:C"]
    set_incoming_edges(g, C, [B])
    assert g.backward_adj["s:C"] == {"s:B"}
    assert all(not (e.upstream.key() == "s:A" and e.downstream.key() == "s:C") for e in g.edges)


def test_remove_dependency() -> None:
    g = DependencyGraph()
    A, B = _r("A"), _r("B")
    add_dependency(g, upstream=A, downstream=B)
    assert remove_dependency(g, A, B) is True
    assert remove_dependency(g, A, B) is False
    assert "s:A" not in g.backward_adj.get("s:B", set())


def test_input_staleness_detects_changed_consumed_upstream() -> None:
    g = DependencyGraph()
    C = _r("C")
    record_consumed(g, C, {"s:A": "A1"})
    assert stale_against_inputs(g, C, {"s:A": "A1"}) is False          # unchanged -> fresh
    assert stale_against_inputs(g, C, {"s:A": "A2"}) is True           # upstream commit changed -> stale


def test_input_staleness_ignores_dropped_dependency() -> None:
    # Consumed A before; this call observes no A (dependency removed) -> not stale (no ghost over-invalidation).
    g = DependencyGraph()
    C = _r("C")
    record_consumed(g, C, {"s:A": "A1"})
    assert stale_against_inputs(g, C, {}) is False
    assert stale_against_inputs(g, C, {"s:B": "B1"}) is False          # only a new, never-consumed upstream


def test_input_staleness_mutual_dependency_without_cycle() -> None:
    # A consumed B@B1; B's output now carries B2 -> A is stale, with no graph edge/cycle needed.
    g = DependencyGraph()
    A = _r("A")
    record_consumed(g, A, {"s:B": "B1"})
    assert stale_against_inputs(g, A, {"s:B": "B2"}) is True
    # after A regenerates against B2 and records it, the same input no longer restales -> settles
    record_consumed(g, A, {"s:B": "B2"})
    assert stale_against_inputs(g, A, {"s:B": "B2"}) is False


def test_dict_result_carries_flow() -> None:
    # Plain dict rejects setattr; attach_producer_flow must wrap it so dict-shaped
    # slot outputs (records, reports, group maps) can carry producer flow downstream.
    flow = create_flow("s1", "slotA", "cA1", output_profile={"type": "dict"})
    tagged = attach_producer_flow({"k": [1, 2, 3]}, flow)
    assert isinstance(tagged, dict)
    assert tagged == {"k": [1, 2, 3]}
    got = extract_flow(tagged)
    assert got is not None and got.producing_slot.slot_id == "slotA"


def test_list_result_still_carries_flow() -> None:
    flow = create_flow("s1", "slotA", "cA1")
    tagged = attach_producer_flow([{"x": 1}], flow)
    assert isinstance(tagged, list) and extract_flow(tagged) is not None


def test_scalar_result_returned_unchanged() -> None:
    # Scalars cannot carry flow; attach must be a transparent no-op (no crash).
    flow = create_flow("s1", "slotA", "cA1")
    assert attach_producer_flow(7, flow) == 7
    assert extract_flow(7) is None


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
