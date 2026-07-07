"""Frontier-kernel Phase 5: branch (guard compilation) and merge (verified
mixture collapse).

Offline: merge's own gates (evidence reproduction, separation search) reuse
existing primitives (interpreted.validate_residual,
decisions.discriminate.search_discriminating_inputs) that are tested in
their own right elsewhere; here they're mocked so these tests exercise only
merge's gate orchestration, matching this repo's convention for freeze's
tests (test_kernel_operators.py).
"""
from __future__ import annotations

import sys

import semipy.decisions.discriminate as discriminate_mod
import semipy.interpreted  # noqa: F401 -- registers the real module in sys.modules
from semipy.decisions.discriminate import DiscriminationResult
from semipy.kernel.operators import BranchEvent, MergeEvent, branch, merge, synthesize_separating_guard

# semipy/__init__.py clobbers the `semipy.interpreted` package attribute with
# the `interpreted()` factory function it re-exports; sys.modules is the
# reliable way to reach the real module for monkeypatching (see
# test_kernel_operators.py for the full explanation).
interpreted_mod = sys.modules["semipy.interpreted"]


# ---------------------------------------------------------------------------
# branch
# ---------------------------------------------------------------------------


def test_branch_licenses_when_at_least_one_guard_compiles():
    result = branch(["msg.kind == 'insert'", "not a valid guard((("])
    assert isinstance(result, BranchEvent)
    assert result.licensed is True
    assert result.guards == ["msg.kind == 'insert'"]
    assert result.rejected_guards == ["not a valid guard((("]


def test_branch_refuses_when_no_guard_compiles():
    result = branch(["not valid (((", "also not valid ((("])
    assert result.licensed is False
    assert result.guards == []
    assert "node stays molten" in result.reason


def test_branch_refuses_on_an_empty_proposal_list():
    result = branch([])
    assert result.licensed is False


# ---------------------------------------------------------------------------
# synthesize_separating_guard (Phase 5, branch's live-wired guard proposal)
# ---------------------------------------------------------------------------


def test_synthesize_separating_guard_finds_a_none_check_via_the_template_bank():
    guard = synthesize_separating_guard(old_input={"x": None}, new_input={"x": 5})
    assert guard == "x is None"


def test_synthesize_separating_guard_finds_a_type_check_via_the_template_bank():
    guard = synthesize_separating_guard(old_input={"x": "hello"}, new_input={"x": 5})
    assert guard == "isinstance(x, str)"


def test_synthesize_separating_guard_finds_an_empty_length_check():
    guard = synthesize_separating_guard(old_input={"items": []}, new_input={"items": [1, 2]})
    assert guard == "len(items) == 0"


def test_synthesize_separating_guard_escalates_to_the_llm_when_no_template_separates(monkeypatch):
    # Same type, same truthiness, no template candidate can tell these apart --
    # only the (mocked) LLM escalation can propose something.
    monkeypatch.setattr(
        interpreted_mod, "synthesize_residual_source",
        lambda *a, **k: "def solve(status):\n    return status == 'archived'\n",
    )
    guard = synthesize_separating_guard(
        old_input={"status": "archived"}, new_input={"status": "active"},
    )
    assert guard == "status == 'archived'"


def test_synthesize_separating_guard_rejects_an_llm_proposal_that_does_not_actually_separate(monkeypatch):
    # Two non-empty strings of the same shape: no template candidate applies, so
    # this reaches the (mocked) LLM escalation. Its proposal is syntactically
    # valid but evaluates the same way on both inputs -- must not be trusted.
    monkeypatch.setattr(
        interpreted_mod, "synthesize_residual_source",
        lambda *a, **k: "def solve(status):\n    return True\n",
    )
    guard = synthesize_separating_guard(
        old_input={"status": "archived"}, new_input={"status": "deleted"},
    )
    assert guard is None


def test_synthesize_separating_guard_returns_none_when_nothing_separates_identical_inputs(monkeypatch):
    monkeypatch.setattr(interpreted_mod, "synthesize_residual_source", lambda *a, **k: None)
    guard = synthesize_separating_guard(old_input={"x": 5}, new_input={"x": 5})
    assert guard is None


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

_EX_A = [((1,), 2), ((2,), 3)]
_EX_B = [((10,), 11), ((20,), 21)]


def test_merge_licenses_when_all_three_gates_pass(monkeypatch):
    monkeypatch.setattr(interpreted_mod, "validate_residual", lambda *a, **k: (True, 1.0))
    monkeypatch.setattr(
        discriminate_mod, "search_discriminating_inputs",
        lambda *a, **k: DiscriminationResult(found=False, base_clusters=1, tried=4),
    )
    result = merge(
        branch_a_source="def f(x):\n    return x + 1  # branch a, a real implementation\n",
        branch_b_source="def f(x):\n    return x + 1  # branch b, a real implementation\n",
        candidate_unified_source="def f(x):\n    return x + 1\n",
        branch_a_examples=_EX_A, branch_b_examples=_EX_B,
        free_variables=["x"],
    )
    assert isinstance(result, MergeEvent)
    assert result.licensed is True
    assert result.unified_source is not None


def test_merge_refuses_when_candidate_does_not_reproduce_evidence(monkeypatch):
    monkeypatch.setattr(interpreted_mod, "validate_residual", lambda *a, **k: (False, 0.5))
    result = merge(
        branch_a_source="def f(x):\n    return x + 1\n",
        branch_b_source="def f(x):\n    return x + 1\n",
        candidate_unified_source="def f(x):\n    return x\n",
        branch_a_examples=_EX_A, branch_b_examples=_EX_B,
        free_variables=["x"],
    )
    assert result.licensed is False
    assert "does not reproduce" in result.reason
    assert result.unified_source is None


def test_merge_refuses_when_separation_search_finds_a_split(monkeypatch):
    monkeypatch.setattr(interpreted_mod, "validate_residual", lambda *a, **k: (True, 1.0))
    monkeypatch.setattr(
        discriminate_mod, "search_discriminating_inputs",
        lambda *a, **k: DiscriminationResult(found=True, base_clusters=1, best_clusters=2, germ="null", tried=3),
    )
    result = merge(
        branch_a_source="def f(x):\n    return x + 1\n",
        branch_b_source="def f(x):\n    return x + 2\n",
        candidate_unified_source="def f(x):\n    return x + 1\n",
        branch_a_examples=_EX_A, branch_b_examples=_EX_B,
        free_variables=["x"],
    )
    assert result.licensed is False
    assert "separation search" in result.reason


def test_merge_refuses_via_mdl_gate_when_union_is_not_shorter(monkeypatch):
    monkeypatch.setattr(interpreted_mod, "validate_residual", lambda *a, **k: (True, 1.0))
    monkeypatch.setattr(
        discriminate_mod, "search_discriminating_inputs",
        lambda *a, **k: DiscriminationResult(found=False, base_clusters=1, tried=4),
    )
    result = merge(
        branch_a_source="def f(x):\n    return x\n",
        branch_b_source="def f(x):\n    return x\n",
        candidate_unified_source="def f(x):\n    return x  # a needlessly verbose unified candidate here\n",
        branch_a_examples=_EX_A, branch_b_examples=_EX_B,
        free_variables=["x"],
    )
    assert result.licensed is False
    assert "MDL gate" in result.reason
