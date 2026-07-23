"""Phase 5: re-open a committed decision under input-population drift.

A branch that pools >1 candidate is only "equivalent over the N tested inputs"
(the ∃-sample-as-∀ caveat). When the observation population later grows a
genuinely new input *pattern* that splits that merged branch, the decision must
re-open so the fork is re-surfaced. These tests exercise the pure re-clustering
logic in ``decisions.reopen`` -- no LLM, candidates are re-run in memory.
"""
from __future__ import annotations

from semipy.decisions.divergence import observe_pure
from semipy.decisions.model import DecisionSet
from semipy.decisions.reopen import reopen_on_input_drift
from semipy.orchestration.roles.decision_classifier import classify_divergence

# c0 and c1 AGREE on the "p" input (both return 'x') but DIFFER on "q";
# c2 always differs. Over "p" alone: 2 classes ({c0,c1} pooled, {c2}).
_C0 = "def f(mode):\n    return 'x' if mode == 'p' else 'a'\n"
_C1 = "def f(mode):\n    return 'x' if mode == 'p' else 'b'\n"
_C2 = "def f(mode):\n    return 'z'\n"
_CANDS = {"c0": _C0, "c1": _C1, "c2": _C2}
_P = {"mode": "p"}
_Q = {"mode": "q"}


def _committed_set() -> DecisionSet:
    """A DecisionSet as it would be committed after observing only the 'p' input:
    c0/c1 merged into one branch, c2 in another (2 behavioral classes)."""
    div = observe_pure(_CANDS, free_variables=["mode"], sample_rows=[_P])
    decisions = classify_divergence(div, germ="mode", example_in=_P, use_llm=False)
    return DecisionSet(
        slot_id="s", decisions=decisions, candidates=dict(_CANDS),
        obs_fingerprint="fp-p-only",
    )


def test_reopen_when_new_input_splits_a_merged_branch():
    dset = _committed_set()
    # Sanity: committed over 'p' alone, c0 and c1 are pooled.
    pooled = [b for d in dset.decisions for b in d.branches if len(b.candidate_ids) > 1]
    assert pooled, "precondition: a merged branch exists at commit"

    reopened = reopen_on_input_drift(
        dset,
        free_variables=["mode"],
        sample_rows=[_P, _Q],  # enlarged population: 'q' splits c0 from c1
        new_fingerprint="fp-p-and-q",
    )
    assert reopened is True
    # The finer partition now has all three candidates in distinct branches.
    branches = [b for d in dset.decisions for b in d.branches]
    assert sum(len(b.candidate_ids) for b in branches) == 3
    assert all(len(b.candidate_ids) == 1 for b in branches)
    # Re-opened decisions render again.
    assert all(d.is_open for d in dset.decisions)
    # The evaluated population is recorded so we don't re-observe at the same fp.
    assert dset.obs_fingerprint == "fp-p-and-q"


def test_no_reopen_when_population_does_not_split():
    dset = _committed_set()
    reopened = reopen_on_input_drift(
        dset,
        free_variables=["mode"],
        sample_rows=[_P],  # same behavioral evidence -> no new class
        new_fingerprint="fp-p-only-again",
    )
    assert reopened is False
    # A merged branch is still merged; nothing re-opened.
    assert any(
        len(b.candidate_ids) > 1 for d in dset.decisions for b in d.branches
    )
    # Fingerprint is still advanced so we don't re-observe this population again.
    assert dset.obs_fingerprint == "fp-p-only-again"
