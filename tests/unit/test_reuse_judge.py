"""U7: reuse-judge vote aggregation. Pure logic, runs offline.

The judge LLM call needs a key (verified live separately). These tests pin the
voting policy: ties bias to ADAPT (MAST under-verification mitigation), a single
vote reproduces that vote's decision (default behavior unchanged), and adapt
feedback is merged from the dissenting judges.
"""
from __future__ import annotations

from semipy.agents.decision import SemanticDecision, aggregate_semantic_votes


def _reuse():
    return SemanticDecision(decision="reuse", reasoning="looks fine")


def _adapt(reason="intent failure", problematic=None):
    return SemanticDecision(
        decision="adapt", reasoning=reason, problematic_inputs=problematic or []
    )


def test_single_vote_reproduces_its_decision():
    assert aggregate_semantic_votes([_reuse()]).decision == "reuse"
    assert aggregate_semantic_votes([_adapt()]).decision == "adapt"


def test_strict_majority_reuse_reuses():
    assert aggregate_semantic_votes([_reuse(), _reuse(), _adapt()]).decision == "reuse"


def test_majority_adapt_adapts():
    assert aggregate_semantic_votes([_adapt(), _adapt(), _reuse()]).decision == "adapt"


def test_tie_biases_to_adapt():
    # Even split -> adapt (bias toward verification).
    assert aggregate_semantic_votes([_reuse(), _adapt()]).decision == "adapt"
    assert aggregate_semantic_votes([_reuse(), _reuse(), _adapt(), _adapt()]).decision == "adapt"


def test_no_votes_defaults_to_reuse():
    # All judges errored/abstained -> trust current impl (cannot judge -> reuse).
    assert aggregate_semantic_votes([None, None]).decision == "reuse"


def test_adapt_merges_problematic_inputs_from_dissent():
    out = aggregate_semantic_votes([
        _adapt("failed A", problematic=["inA"]),
        _adapt("failed B", problematic=["inB"]),
        _reuse(),
    ])
    assert out.decision == "adapt"
    assert set(out.problematic_inputs) == {"inA", "inB"}
    assert "failed A" in out.reasoning and "failed B" in out.reasoning
