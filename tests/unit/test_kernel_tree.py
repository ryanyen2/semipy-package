"""Frontier-kernel Phase 1: the hardness tree schema, its round-trip, and the
general combinator recognizer's multi-node go/no-go measurement.

The recognizer must generalize across domains: the same map/filter/fold/branch
matchers should fire whether the underlying task is numeric formatting, log
routing, or merge-conflict resolution. The corpus below is deliberately
multi-domain so a single-example win cannot be mistaken for generality.
"""
from __future__ import annotations

import textwrap

from semipy.history.version_control import Slot
from semipy.kernel.tree import (
    Hardness,
    Node,
    NodeKind,
    degenerate_tree,
    get_tree,
    is_multi_node,
    lower_source_to_tree,
    multi_node_fraction,
    save_tree,
    tree_from_dict,
    tree_to_dict,
)
from semipy.store import load_portal, save_portal


# ---------------------------------------------------------------------------
# Schema basics
# ---------------------------------------------------------------------------


def test_degenerate_tree_is_a_single_opaque_node():
    node = degenerate_tree("slot1", "def f(x):\n    return x", hardness=Hardness.PLASTIC)
    assert node.kind == NodeKind.OPAQUE
    assert node.hardness == Hardness.PLASTIC
    assert node.is_leaf()
    assert list(node.walk()) == [node]
    assert not is_multi_node(node)


def test_node_walk_is_preorder():
    leaf_a = Node(node_id="a", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC)
    leaf_b = Node(node_id="b", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC)
    root = Node(node_id="root", kind=NodeKind.COMPOSE, hardness=Hardness.PLASTIC, children=[leaf_a, leaf_b])
    assert [n.node_id for n in root.walk()] == ["root", "a", "b"]


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


def test_tree_dict_round_trip_preserves_structure_and_guards():
    src = textwrap.dedent(
        """
        def resolve(msg):
            if msg.kind == "insert":
                return apply_insert(msg)
            elif msg.kind == "delete":
                return apply_delete(msg)
            else:
                return manual_merge(msg)
        """
    )
    node = lower_source_to_tree(src, "slotX")
    restored = tree_from_dict(tree_to_dict(node))
    assert restored.kind == node.kind == NodeKind.BRANCH
    assert [g.predicate_source for g in restored.guards] == [g.predicate_source for g in node.guards]
    assert [g.is_fallback for g in restored.guards] == [False, False, True]
    assert len(restored.children) == len(node.children) == 3


def test_portal_round_trips_kernel_tree(tmp_path):
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    portal = load_portal(cache_dir, "sess", "f.py", "mod")
    slot = Slot(slot_id="s1", call_site_info={}, function_name_base="f")
    portal.slots["s1"] = slot

    tree = degenerate_tree("s1", "def f(x):\n    return x + 1", hardness=Hardness.PLASTIC)
    save_tree(slot, tree)
    save_portal(cache_dir, portal)

    reloaded = load_portal(cache_dir, "sess", "f.py", "mod")
    restored = get_tree(reloaded.slots["s1"])
    assert restored is not None
    assert restored.kind == NodeKind.OPAQUE
    assert restored.artifact == "def f(x):\n    return x + 1"


def test_get_tree_returns_none_for_legacy_slot_with_no_persisted_tree():
    slot = Slot(slot_id="legacy", call_site_info={}, function_name_base="f")
    assert get_tree(slot) is None


# ---------------------------------------------------------------------------
# Recognizer correctness -- one test per combinator shape.
# ---------------------------------------------------------------------------


def test_recognizes_map_shape():
    src = "def f(items):\n    out = []\n    for x in items:\n        out.append(x * 2)\n    return out\n"
    node = lower_source_to_tree(src, "s")
    kinds = [n.kind for n in node.walk()]
    assert NodeKind.MAP in kinds
    assert is_multi_node(node)


def test_recognizes_filter_shape():
    src = "def f(items):\n    out = []\n    for x in items:\n        if x > 0:\n            out.append(x)\n    return out\n"
    node = lower_source_to_tree(src, "s")
    kinds = [n.kind for n in node.walk()]
    assert NodeKind.FILTER in kinds
    assert NodeKind.MAP not in kinds  # unconditional append of the bare item is filter, not map
    assert is_multi_node(node)


def test_recognizes_combined_filter_and_map_shape():
    src = "def f(items):\n    out = []\n    for x in items:\n        if x > 0:\n            out.append(x * 2)\n    return out\n"
    node = lower_source_to_tree(src, "s")
    kinds = {n.kind for n in node.walk()}
    assert {NodeKind.FILTER, NodeKind.MAP}.issubset(kinds)


def test_recognizes_fold_shape_with_reassignment_and_augassign():
    src_reassign = "def f(items):\n    total = 0\n    for x in items:\n        total = total + x\n    return total\n"
    src_aug = "def f(items):\n    total = 0\n    for x in items:\n        total += x\n    return total\n"
    for src in (src_reassign, src_aug):
        node = lower_source_to_tree(src, "s")
        assert NodeKind.FOLD in [n.kind for n in node.walk()]


def test_recognizes_branch_by_data_type():
    """The user's first example: 'changing algorithm based on data type.'"""
    src = textwrap.dedent(
        """
        def normalize(x):
            if isinstance(x, int):
                return format_int(x)
            elif isinstance(x, str):
                return format_str(x)
            else:
                return str(x)
        """
    )
    node = lower_source_to_tree(src, "s")
    assert node.kind == NodeKind.BRANCH
    assert node.guards[0].predicate_source == "isinstance(x, int)"
    assert node.guards[1].predicate_source == "isinstance(x, str)"
    assert node.guards[2].is_fallback


def test_recognizes_branch_by_message_kind():
    """The user's second example: 'merge conflict algo based on message type.'"""
    src = textwrap.dedent(
        """
        def resolve_conflict(msg):
            if msg.kind == "insert":
                return apply_insert(msg)
            elif msg.kind == "delete":
                return apply_delete(msg)
            else:
                return manual_merge(msg)
        """
    )
    node = lower_source_to_tree(src, "s")
    assert node.kind == NodeKind.BRANCH
    assert [g.predicate_source for g in node.guards[:2]] == ["msg.kind == 'insert'", "msg.kind == 'delete'"]


def test_recognizes_branch_by_arbitrary_third_domain_log_severity():
    """A third, unrelated domain -- same general recognizer, no special-casing."""
    src = textwrap.dedent(
        """
        def route(entry):
            if entry.level == "error":
                return page_oncall(entry)
            elif entry.level == "warning":
                return queue_for_review(entry)
            else:
                return archive(entry)
        """
    )
    node = lower_source_to_tree(src, "s")
    assert node.kind == NodeKind.BRANCH
    assert len(node.children) == 3


def test_recognizes_comprehension_map_and_filter():
    node = lower_source_to_tree("def f(items):\n    return [x * 2 for x in items if x > 0]\n", "s")
    kinds = {n.kind for n in node.walk()}
    assert {NodeKind.FILTER, NodeKind.MAP}.issubset(kinds)


def test_recognizes_multi_stage_compose_pipeline():
    src = textwrap.dedent(
        """
        def f(items):
            kept = []
            for x in items:
                if x > 0:
                    kept.append(x)
            total = 0
            for y in kept:
                total = total + y
            return total
        """
    )
    node = lower_source_to_tree(src, "s")
    kinds = {n.kind for n in node.walk()}
    assert node.kind == NodeKind.COMPOSE
    assert {NodeKind.FILTER, NodeKind.FOLD}.issubset(kinds)


# ---------------------------------------------------------------------------
# Fallback correctness -- lowering must never misclassify or raise.
# ---------------------------------------------------------------------------


def test_recursive_control_flow_falls_back_to_opaque():
    src = "def f(n):\n    if n <= 1:\n        return 1\n    return n * f(n - 1)\n"
    node = lower_source_to_tree(src, "s")
    assert node.kind == NodeKind.OPAQUE
    assert not is_multi_node(node)


def test_plain_expression_pipeline_falls_back_to_opaque():
    node = lower_source_to_tree("def f(s):\n    return s.strip().lower()\n", "s")
    assert node.kind == NodeKind.OPAQUE


def test_tuple_unpacking_loop_target_falls_back_to_opaque():
    src = "def f(pairs):\n    out = []\n    for a, b in pairs:\n        out.append(a + b)\n    return out\n"
    node = lower_source_to_tree(src, "s")
    assert not is_multi_node(node)  # documented Phase-1 scope limit, not silently wrong


def test_lowering_never_raises_on_unparseable_or_empty_source():
    for bad in ("", "def f(:\n    pass", "not even python {{{", "x = 1\n"):
        node = lower_source_to_tree(bad, "s")
        assert node.kind == NodeKind.OPAQUE
        assert node.artifact == bad


# ---------------------------------------------------------------------------
# Phase 1 go/no-go: multi-node fraction over a representative, multi-domain
# corpus. This stands in for "the existing slot corpus" from the plan (Part
# III §6) -- measuring the *live* generated-slot corpus requires running real
# LLM generations, which this offline suite does not do.
# ---------------------------------------------------------------------------

_REPRESENTATIVE_CORPUS: dict[str, str] = {
    "numeric_map": "def f(xs):\n    out = []\n    for x in xs:\n        out.append(x * 2)\n    return out\n",
    "string_map": "def f(xs):\n    out = []\n    for x in xs:\n        out.append(x.upper())\n    return out\n",
    "filter_positive": "def f(xs):\n    out = []\n    for x in xs:\n        if x > 0:\n            out.append(x)\n    return out\n",
    "filter_nonempty_strings": "def f(xs):\n    out = []\n    for x in xs:\n        if x:\n            out.append(x)\n    return out\n",
    "fold_sum": "def f(xs):\n    total = 0\n    for x in xs:\n        total += x\n    return total\n",
    "fold_concat": 'def f(xs):\n    acc = ""\n    for x in xs:\n        acc = acc + x\n    return acc\n',
    "branch_by_data_type": textwrap.dedent(
        """
        def normalize(x):
            if isinstance(x, int):
                return format_int(x)
            elif isinstance(x, str):
                return format_str(x)
            else:
                return str(x)
        """
    ),
    "branch_by_message_kind": textwrap.dedent(
        """
        def resolve_conflict(msg):
            if msg.kind == "insert":
                return apply_insert(msg)
            elif msg.kind == "delete":
                return apply_delete(msg)
            else:
                return manual_merge(msg)
        """
    ),
    "branch_by_log_severity": textwrap.dedent(
        """
        def route(entry):
            if entry.level == "error":
                return page_oncall(entry)
            elif entry.level == "warning":
                return queue_for_review(entry)
            else:
                return archive(entry)
        """
    ),
    "compose_filter_then_map": "def f(xs):\n    return [x * 2 for x in xs if x > 0]\n",
    "compose_filter_then_fold": textwrap.dedent(
        """
        def f(xs):
            kept = []
            for x in xs:
                if x > 0:
                    kept.append(x)
            total = 0
            for y in kept:
                total = total + y
            return total
        """
    ),
    "opaque_recursive_factorial": "def f(n):\n    if n <= 1:\n        return 1\n    return n * f(n - 1)\n",
    "opaque_expression_pipeline": "def f(s):\n    return s.strip().lower()\n",
    "opaque_tuple_unpacking_loop": "def f(pairs):\n    out = []\n    for a, b in pairs:\n        out.append(a + b)\n    return out\n",
}


def test_multi_node_fraction_over_representative_corpus_is_reported():
    fraction = multi_node_fraction(list(_REPRESENTATIVE_CORPUS.values()))
    print(f"\n[frontier-kernel Phase 1] multi-node fraction over representative corpus: {fraction:.2f}")

    # The three genuinely opaque, unrecognizable-by-design entries must not
    # inflate the count; the rest (a majority, spanning three unrelated
    # domains for BRANCH alone) must be recognized.
    expected_opaque = {
        "opaque_recursive_factorial",
        "opaque_expression_pipeline",
        "opaque_tuple_unpacking_loop",
    }
    n = len(_REPRESENTATIVE_CORPUS)
    n_opaque = len(expected_opaque)
    assert fraction == (n - n_opaque) / n
