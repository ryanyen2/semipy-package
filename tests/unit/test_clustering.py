"""`#?` branch weights: vote share over the drawn candidates.

cluster_signatures weights each branch by ``len(members) / total`` -- the
fraction of drawn candidates that exhibited that behavior. This is an honest
vote share, not a calibrated probability. See semipy/decisions/cluster.py.
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


def test_weights_are_candidate_vote_share():
    clusters = cluster_signatures(_SIGS)
    weights = {frozenset(c.candidate_ids): c.weight for c in clusters}
    assert weights[frozenset({"c0", "c1", "c2"})] == 3 / 5
    assert weights[frozenset({"c3", "c4"})] == 2 / 5


def test_weights_sum_to_one():
    clusters = cluster_signatures(_SIGS)
    assert math.isclose(sum(c.weight for c in clusters), 1.0)


def test_empty_signatures_returns_no_clusters():
    assert cluster_signatures({}) == []
