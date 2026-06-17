"""U11: determinism, cost guard, decision-structure, comparability."""
from __future__ import annotations

import time

from semipy.decisions.runmodes import (
    CostGuard,
    assess_comparability,
    cluster_by_decision_structure,
    collect_within_budget,
    is_reproducible,
    observe_seeded,
)

_RANDOM = "def f(rows):\n    import random\n    return [random.random() for _ in range(3)]\n"

# Two "model training" candidates: same structure (a feature choice + a weight
# vector) but different chosen feature. Weights are volatile; the feature is the
# decision.
_TRAIN_X = "def f(rows):\n    import random\n    return {'feature': 'x', 'weights': [random.random() for _ in range(3)]}\n"
_TRAIN_X2 = "def f(rows):\n    import random\n    return {'feature': 'x', 'weights': [random.random()*9 for _ in range(3)]}\n"
_TRAIN_Y = "def f(rows):\n    import random\n    return {'feature': 'y', 'weights': [random.random() for _ in range(3)]}\n"

_OPAQUE = "def f(rows):\n    class X:\n        pass\n    return X()\n"

_ROWS = [{"rows": [1, 2, 3]}]


def test_seeded_run_is_reproducible():
    r1 = observe_seeded({"c": _RANDOM}, free_variables=["rows"], sample_rows=_ROWS, seed=0)
    r2 = observe_seeded({"c": _RANDOM}, free_variables=["rows"], sample_rows=_ROWS, seed=0)
    assert r1.runs["c"].signature == r2.runs["c"].signature
    assert r1.runs["c"].signature != ("__unrunnable__",)


def test_model_training_clusters_on_chosen_structure_not_weights():
    div = observe_seeded(
        {"x1": _TRAIN_X, "x2": _TRAIN_X2, "y": _TRAIN_Y},
        free_variables=["rows"],
        sample_rows=_ROWS,
        seed=0,
    )
    clusters = cluster_by_decision_structure(div)
    # Two structural branches: feature 'x' (x1, x2) and feature 'y'.
    assert len(clusters) == 2
    heavy = max(clusters, key=lambda c: c.weight)
    assert set(heavy.candidate_ids) == {"x1", "x2"}


def test_cost_guard_bounds_collection_not_hang():
    over = CostGuard(budget_s=-1.0)  # already exceeded
    results, limited = collect_within_budget([lambda: 1, lambda: 2, lambda: 3], over)
    assert limited and results == []

    ample = CostGuard(budget_s=100.0)
    results, limited = collect_within_budget([lambda: 1, lambda: 2], ample)
    assert not limited and results == [1, 2]


def test_cost_guard_reports_elapsed_and_exceeded():
    g = CostGuard(budget_s=0.0)
    time.sleep(0.01)
    assert g.exceeded
    assert g.elapsed >= 0.0


def test_no_comparable_signal_for_nonreproducible_output():
    # An object with a default repr (memory address) is not reproducible.
    assert not is_reproducible(_OPAQUE, free_variables=["rows"], sample_rows=_ROWS)
    report = assess_comparability({"c": _OPAQUE}, free_variables=["rows"], sample_rows=_ROWS)
    assert not report.comparable
    assert "non-reproducible" in report.reason


def test_comparable_signal_for_seeded_random():
    report = assess_comparability({"c": _RANDOM}, free_variables=["rows"], sample_rows=_ROWS)
    assert report.comparable
