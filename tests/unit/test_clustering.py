"""Probability-weighted `#?` branch weights (semantic-entropy style aggregation).

cluster_signatures's weight computation: naive vote-count by default, switching to
a softmax over candidate log-probs only when *every* candidate has a score (the
binary fallback rule) -- see semipy/decisions/cluster.py.
"""
from __future__ import annotations

import math

from semipy.decisions.cluster import cluster_signatures

_SIGS = {
    "c0": ("ok:1",),
    "c1": ("ok:1",),
    "c2": ("ok:1",),
    "c3": ("ok:2",),
    "c4": ("ok:2",),
}


def test_no_scores_keeps_naive_vote_weights():
    clusters = cluster_signatures(_SIGS)
    weights = {frozenset(c.candidate_ids): c.weight for c in clusters}
    assert weights[frozenset({"c0", "c1", "c2"})] == 3 / 5
    assert weights[frozenset({"c3", "c4"})] == 2 / 5


def test_partial_scores_falls_back_to_naive():
    # c4 has no score at all -- the binary rule requires every candidate to have
    # one, so this must weight exactly like the no-scores case.
    scores = {"c0": -0.1, "c1": -0.2, "c2": -0.3, "c3": -1.0}
    clusters = cluster_signatures(_SIGS, scores=scores)
    weights = {frozenset(c.candidate_ids): c.weight for c in clusters}
    assert weights[frozenset({"c0", "c1", "c2"})] == 3 / 5
    assert weights[frozenset({"c3", "c4"})] == 2 / 5


def test_full_scores_match_hand_computed_softmax():
    scores = {"c0": -0.1, "c1": -0.2, "c2": -0.3, "c3": -4.0, "c4": -4.5}
    clusters = cluster_signatures(_SIGS, scores=scores)
    m = max(scores.values())
    exp_scores = {cid: math.exp(s - m) for cid, s in scores.items()}
    total_exp = sum(exp_scores.values())
    expected_heavy = (exp_scores["c0"] + exp_scores["c1"] + exp_scores["c2"]) / total_exp
    expected_light = (exp_scores["c3"] + exp_scores["c4"]) / total_exp

    weights = {frozenset(c.candidate_ids): c.weight for c in clusters}
    assert math.isclose(weights[frozenset({"c0", "c1", "c2"})], expected_heavy)
    assert math.isclose(weights[frozenset({"c3", "c4"})], expected_light)
    # A confident majority should now carry noticeably more than its 3/5 vote share.
    assert expected_heavy > 3 / 5


def test_full_scores_still_sum_to_one():
    scores = {"c0": -0.5, "c1": -1.0, "c2": -1.5, "c3": -2.0, "c4": -2.5}
    clusters = cluster_signatures(_SIGS, scores=scores)
    assert math.isclose(sum(c.weight for c in clusters), 1.0)


def test_empty_signatures_returns_no_clusters():
    assert cluster_signatures({}) == []
    assert cluster_signatures({}, scores={}) == []
