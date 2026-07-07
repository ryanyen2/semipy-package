"""Frontier-kernel Phase 4/5, live-wired: melt inside the contract gate's retry
loop, and branch as an alternative to quarantining a case (both opt-in via
config.melt_on_contract_failure / config.branch_on_quarantine).

These orchestrate the pure kernel primitives (kernel.blame.blame + kernel.
operators.melt; kernel.operators.branch/synthesize_separating_guard +
kernel.tree.build_branch_wrapper) against real ContractCase-shaped evidence.
The primitives themselves are covered in test_kernel_blame.py,
test_kernel_melt.py, test_kernel_tree.py, and test_kernel_branch_merge.py;
here we only test the two slot_resolver-level decision functions that decide
*whether* and *how* to invoke them.
"""
from __future__ import annotations

import sys

from semipy.contract.models import ContractCase
from semipy.slot_resolver import _try_branch_split, _try_melt_for_example_case
from semipy.types import SlotCategory, SlotSpec

# semipy/__init__.py clobbers the `semipy.interpreted` package attribute with
# the `interpreted()` factory function it re-exports; sys.modules is the
# reliable way to reach the real module for monkeypatching (see
# test_kernel_operators.py / test_kernel_branch_merge.py for the same pattern).
interpreted_mod = sys.modules["semipy.interpreted"]


def _slot_spec(slot_id: str = "s1") -> SlotSpec:
    return SlotSpec(
        slot_id=slot_id,
        source_span=("f.py", 1, 1),
        spec_text="double every item",
        spec_hash="h",
        spec_equivalence_key="h",
        free_variables=["items"],
        control_context="",
        expected_category=SlotCategory.EXPRESSION_STANDALONE,
        expected_type=None,
        output_names=[],
        formal_constraints=[],
        usage_hints=[],
        enclosing_function_qualname="f",
        enclosing_function_span=(1, 1),
        enclosing_function_source="def f(items): pass",
    )


# ---------------------------------------------------------------------------
# _try_melt_for_example_case
# ---------------------------------------------------------------------------

_MAP_CANDIDATE = (
    "def f(items):\n"
    "    out = []\n"
    "    for x in items:\n"
    "        out.append(x * 2)\n"
    "    return out\n"
)


def test_melt_patches_only_the_blamed_map_leaf(monkeypatch):
    monkeypatch.setattr(
        interpreted_mod, "synthesize_residual_source",
        lambda *a, **k: "def solve(x):\n    return x * 3\n",
    )
    case = ContractCase(
        case_id="case1", kind="example",
        input_sample={"items": [1, 2, 3]},
        expected_repr=repr([2, 4, 999]),  # wrong on the last element
    )
    patched = _try_melt_for_example_case(case=case, candidate_source=_MAP_CANDIDATE, slot_spec=_slot_spec())
    assert patched is not None
    assert "out.append(x * 3)" in patched
    assert "def f(items):" in patched


def test_melt_returns_none_for_a_non_example_case():
    case = ContractCase(case_id="c", kind="invariant", input_sample={"items": [1]})
    assert _try_melt_for_example_case(case=case, candidate_source=_MAP_CANDIDATE, slot_spec=_slot_spec()) is None


def test_melt_returns_none_when_expected_repr_is_not_literal_evalable():
    case = ContractCase(
        case_id="c", kind="example", input_sample={"items": [1, 2, 3]},
        expected_repr="<DataFrame object at 0x...>",
    )
    assert _try_melt_for_example_case(case=case, candidate_source=_MAP_CANDIDATE, slot_spec=_slot_spec()) is None


def test_melt_returns_none_when_blame_cannot_localize_past_the_root():
    case = ContractCase(case_id="c", kind="example", input_sample={"x": 1}, expected_repr=repr(999))
    candidate = "def f(x):\n    return complicated_thing(x)\n"
    assert _try_melt_for_example_case(case=case, candidate_source=candidate, slot_spec=_slot_spec()) is None


def test_melt_returns_none_when_leaf_synthesis_yields_nothing(monkeypatch):
    monkeypatch.setattr(interpreted_mod, "synthesize_residual_source", lambda *a, **k: None)
    case = ContractCase(
        case_id="c", kind="example",
        input_sample={"items": [1, 2, 3]},
        expected_repr=repr([2, 4, 999]),
    )
    assert _try_melt_for_example_case(case=case, candidate_source=_MAP_CANDIDATE, slot_spec=_slot_spec()) is None


# ---------------------------------------------------------------------------
# _try_branch_split
# ---------------------------------------------------------------------------


def test_branch_split_preserves_the_old_case_behind_a_template_guard():
    case = ContractCase(case_id="old_case", kind="example", input_sample={"x": None})
    wrapped = _try_branch_split(
        case=case, runtime_values={"x": 5},
        parent_source="def f(x):\n    return -1\n",     # what the old case needs (x is None)
        candidate_source="def f(x):\n    return x + 1\n",  # what the new evidence needs
    )
    assert wrapped is not None
    assert "is None" in wrapped
    assert "_f__regime_old" in wrapped and "_f__regime_new" in wrapped

    ns: dict = {}
    exec(compile(wrapped, "<wrapped>", "exec"), ns)
    f = ns["f"]
    assert f(None) == -1
    assert f(5) == 6


def test_branch_split_returns_none_when_no_guard_separates_the_inputs(monkeypatch):
    monkeypatch.setattr(interpreted_mod, "synthesize_residual_source", lambda *a, **k: None)
    case = ContractCase(case_id="old_case", kind="example", input_sample={"x": 5})
    wrapped = _try_branch_split(
        case=case, runtime_values={"x": 5},  # identical input -- nothing can separate them
        parent_source="def f(x):\n    return x\n",
        candidate_source="def f(x):\n    return x + 1\n",
    )
    assert wrapped is None


def test_branch_split_returns_none_for_a_non_example_case():
    case = ContractCase(case_id="c", kind="invariant", input_sample={"x": None})
    wrapped = _try_branch_split(
        case=case, runtime_values={"x": 5},
        parent_source="def f(x):\n    return -1\n",
        candidate_source="def f(x):\n    return x + 1\n",
    )
    assert wrapped is None
