"""Frontier-kernel Phase 4: melt -- local rejuvenation (blame + patch_source)."""
from __future__ import annotations

from semipy.kernel.operators import melt
from semipy.kernel.tree import lower_source_to_tree, patch_source


def test_melt_patches_just_the_blamed_map_leaf_and_leaves_the_rest_untouched():
    src = "def f(items):\n    out = []\n    for x in items:\n        out.append(x * 2)\n    return out\n"
    result = melt(
        src, "s",
        free_variables={"items": [1, 2, 3]},
        expected_output=[2, 4, 999],  # wrong on the last element
        new_node_source="def map_body(x):\n    return x * 3\n",
    )
    assert result.blamed_kind == "map"
    assert result.blamed_node_id.endswith(".map.body") is False  # blame stops at the MAP node, not its leaf
    assert result.patched_source is not None
    assert "out.append(x * 3)" in result.patched_source
    assert "def f(items):" in result.patched_source  # rest of the shape preserved
    assert 0.0 < result.locality < 1.0


def test_melt_descends_through_branch_and_patches_the_matched_arm():
    src = (
        "def f(x):\n"
        "    if isinstance(x, int):\n"
        "        return x + 1\n"
        "    else:\n"
        "        return x\n"
    )
    result = melt(
        src, "s",
        free_variables={"x": 5},
        expected_output=999,  # any wrong value forces a blame past the branch
        new_node_source="def f(x):\n    return x + 100\n",
    )
    assert result.blamed_node_id == "s.branch.0"
    assert result.patched_source is not None
    assert "return x + 100" in result.patched_source
    assert result.patched_source.strip().endswith("return x")  # the untouched else-arm survives verbatim


def test_melt_localizes_into_a_verified_accumulator_passthrough_fold_segment():
    src = "def f(items):\n    total = 0\n    for x in items:\n        total += x\n    return total\n"
    result = melt(
        src, "s",
        free_variables={"items": [1, 2, 3]},
        expected_output=999,
        # a replacement for the whole for-loop segment (not the whole function):
        # the fold node is blamed whole (§ "no contract, no blame"), but since
        # `total = 0` / `return total` are verified pure passthrough, only the
        # loop itself needs replacing.
        new_node_source="def f(items):\n    for x in items:\n        total = total * 2 + x\n",
    )
    assert result.blamed_kind == "fold"
    assert result.blamed_node_id != "s"  # descended past the root COMPOSE into the fold segment
    assert result.patched_source is not None
    assert "total = total * 2 + x" in result.patched_source
    assert result.patched_source.strip().startswith("def f(items):")
    assert "total = 0" in result.patched_source and "return total" in result.patched_source


def test_melt_falls_back_to_none_for_a_bare_opaque_root():
    src = "def f(x):\n    return complicated_thing(x)\n"
    result = melt(
        src, "s",
        free_variables={"x": 1},
        expected_output=999,
        new_node_source="def f(x):\n    return complicated_thing(x) + 1\n",
    )
    assert result.blamed_node_id == "s"
    assert result.patched_source is None  # blame never leaves the root here; caller regenerates wholesale


def test_melt_locality_is_one_when_the_whole_function_is_blamed():
    src = "def f(x):\n    return x\n"  # single opaque node -- blame stops immediately at the root
    result = melt(
        src, "s",
        free_variables={"x": 1},
        expected_output=2,
        new_node_source="def f(x):\n    return x + 1\n",
    )
    assert result.blamed_node_id == "s"
    assert result.locality == 1.0
    assert result.patched_source is None  # melt never patches at the root; caller regenerates wholesale


def test_patch_source_round_trips_through_lower_source_to_tree_for_a_filter_leaf():
    src = "def f(items):\n    return [x for x in items if x > 0]\n"
    node = lower_source_to_tree(src, "s")
    pred_id = next(n.node_id for n in node.walk() if n.node_id.endswith(".filter.pred"))
    patched = patch_source(src, "s", pred_id, "def filter_pred(x):\n    return x >= 0\n")
    assert patched is not None
    assert "x >= 0" in patched


def test_patch_source_returns_none_for_an_unknown_target_id():
    src = "def f(items):\n    return [x * 2 for x in items]\n"
    assert patch_source(src, "s", "s.not.a.real.id", "def map_body(x):\n    return x\n") is None
