"""Frontier-kernel Phase 4: trace replay + shallowest-failing-node blame."""
from __future__ import annotations

from semipy.kernel.blame import blame, locality_metric
from semipy.kernel.tree import Guard, Hardness, Node, NodeKind, lower_source_to_tree


def test_blame_localizes_map_and_reports_the_offending_element():
    src = "def f(items):\n    return [x * 2 for x in items]\n"
    node = lower_source_to_tree(src, "s")
    assert node.kind == NodeKind.MAP  # bare comprehension lowers to a root-level MAP

    result = blame(node, free_variables={"items": [1, 2, 3]}, expected_output=[2, 4, 999])
    assert result.node_id == node.node_id
    assert result.offending_input == 3
    assert result.replayed_output == [2, 4, 6]


def test_blame_confirms_node_matches_when_there_is_no_divergence():
    src = "def f(items):\n    return [x * 2 for x in items]\n"
    node = lower_source_to_tree(src, "s")
    result = blame(node, free_variables={"items": [1, 2, 3]}, expected_output=[2, 4, 6])
    assert "not under this node" in result.reason


def test_blame_reports_the_expected_target_for_a_map_divergence():
    src = "def f(items):\n    return [x * 2 for x in items]\n"
    node = lower_source_to_tree(src, "s")
    result = blame(node, free_variables={"items": [1, 2, 3]}, expected_output=[2, 4, 999])
    assert result.offending_input == 3
    assert result.offending_target == 999  # what the leaf should have produced for 3


def test_blame_localizes_filter_and_reports_the_offending_element():
    src = "def f(items):\n    return [x for x in items if x > 0]\n"
    node = lower_source_to_tree(src, "s")
    assert node.kind == NodeKind.FILTER

    result = blame(node, free_variables={"items": [1, -1, 2]}, expected_output=[1])
    assert result.offending_input == 2  # should have been included but was dropped from expected
    assert result.offending_target is False  # 2 should NOT have passed the predicate


def test_blame_offending_target_is_none_when_there_is_no_divergence():
    src = "def f(items):\n    return [x * 2 for x in items]\n"
    node = lower_source_to_tree(src, "s")
    result = blame(node, free_variables={"items": [1, 2, 3]}, expected_output=[2, 4, 6])
    assert result.offending_target is None


def test_blame_descends_through_branch_into_the_matched_arm():
    src = (
        "def f(x):\n"
        "    if isinstance(x, int):\n"
        "        return x + 1\n"
        "    else:\n"
        "        return x\n"
    )
    node = lower_source_to_tree(src, "s")
    assert node.kind == NodeKind.BRANCH

    # x=5 matches the isinstance(x, int) arm, whose body is a plain `return x + 1`
    # -- an OPAQUE leaf, not further replayable in isolation, so blame descends
    # one level (past the branch) and then honestly stops there.
    result = blame(node, free_variables={"x": 5}, expected_output=6)
    assert result.node_id != node.node_id  # descended past the branch itself
    assert "not independently replayable" in result.reason


def test_blame_falls_back_whole_for_opaque_and_fold():
    fold_src = "def f(items):\n    total = 0\n    for x in items:\n        total += x\n    return total\n"
    tree = lower_source_to_tree(fold_src, "s")
    fold_node = next(n for n in tree.walk() if n.kind == NodeKind.FOLD)
    result = blame(fold_node, free_variables={"items": [1, 2, 3]}, expected_output=999)
    assert result.node_id == fold_node.node_id
    assert "not independently replayable" in result.reason

    opaque_node = Node(node_id="s", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact="def f(x):\n    return x\n")
    result2 = blame(opaque_node, free_variables={"x": 1}, expected_output=2)
    assert result2.node_id == "s"
    assert "not independently replayable" in result2.reason


def test_blame_falls_back_when_iterable_is_not_a_bare_free_variable():
    # Simulates a MAP node whose iterable expression references something this
    # module cannot resolve (e.g. it depends on a preceding OPAQUE segment).
    leaf = Node(node_id="s.map.body", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact="def map_body(x):\n    return x * 2\n")
    node = Node(node_id="s", kind=NodeKind.MAP, hardness=Hardness.PLASTIC, children=[leaf], meta={"iterable": "derived_list"})
    result = blame(node, free_variables={"items": [1, 2, 3]}, expected_output=[2, 4, 6])
    assert result.node_id == "s"
    assert "out of scope" in result.reason


def test_blame_falls_back_when_no_guard_matches():
    leaf = Node(node_id="s.arm", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact="def f(x):\n    return x\n")
    node = Node(
        node_id="s", kind=NodeKind.BRANCH, hardness=Hardness.PLASTIC,
        guards=[Guard(predicate_source="x > 100")], children=[leaf],
    )
    result = blame(node, free_variables={"x": 1}, expected_output=1)
    assert result.node_id == "s"
    assert "no guard matched" in result.reason


def test_locality_metric_is_small_for_a_deep_blame_and_one_for_the_root():
    src = (
        "def f(x):\n"
        "    if isinstance(x, int):\n"
        "        return x + 1\n"
        "    else:\n"
        "        return x\n"
    )
    node = lower_source_to_tree(src, "s")
    result = blame(node, free_variables={"x": 5}, expected_output=6)
    metric = locality_metric(node, result.node_id)
    assert 0.0 < metric < 1.0

    assert locality_metric(node, node.node_id) == 1.0
    assert locality_metric(node, "no-such-node") == 1.0
