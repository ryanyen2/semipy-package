"""Phase 4: honest completeness accounting on a divergence.

A divergence proves a *lower bound* on the number of distinct behaviors: every
split is a real executed difference, but two candidates that co-cluster are only
"equivalent over the N tested inputs", never proven identical. These tests lock
in that the residual gap (candidates not proven distinct) and the per-branch
"equivalent over N" caveat are reported.
"""
from __future__ import annotations

from semipy.decisions.divergence import observe_pure
from semipy.orchestration.roles.decision_classifier import classify_divergence

_SKIP = "def avg(rows):\n    v=[r['c'] for r in rows if r['c'] is not None]\n    return round(sum(v)/len(v),4) if v else None\n"
_ZERO = "def avg(rows):\n    v=[(r['c'] or 0) for r in rows]\n    return round(sum(v)/len(v),4) if v else None\n"
_NULL = [{"rows": [{"c": 0.4}, {"c": None}, {"c": 0.7}]}]


def test_residual_gap_counts_candidates_not_proven_distinct():
    # Two identical candidates co-cluster; the third splits off. Three candidates,
    # two proven-distinct classes -> a residual gap of one.
    res = observe_pure(
        {"a": _SKIP, "b": _SKIP, "c": _ZERO},
        free_variables=["rows"],
        sample_rows=_NULL,
    )
    assert res.tested_inputs == 1
    assert res.n_candidates == 3
    assert len(res.clusters) == 2
    assert res.residual_gap == 1


def test_all_distinct_leaves_no_gap():
    res = observe_pure(
        {"a": _SKIP, "c": _ZERO},
        free_variables=["rows"],
        sample_rows=_NULL,
    )
    assert res.residual_gap == 0  # every candidate its own proven-distinct class


def test_branch_carries_equivalent_over_n_only_when_it_pools_candidates():
    res = observe_pure(
        {"a": _SKIP, "b": _SKIP, "c": _ZERO},
        free_variables=["rows"],
        sample_rows=_NULL,
    )
    decisions = classify_divergence(res, use_llm=False)
    branches = [b for d in decisions for b in d.branches]
    pooled = next(b for b in branches if len(b.candidate_ids) > 1)
    solo = next(b for b in branches if len(b.candidate_ids) == 1)
    assert pooled.equivalent_over_n == 1  # a,b only equivalent over the tested input
    assert solo.equivalent_over_n is None
    # Round-trips through persistence.
    assert pooled.to_dict()["equivalent_over_n"] == 1
