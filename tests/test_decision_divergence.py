"""U3: divergence observation and N-way clustering (pure slots)."""
from __future__ import annotations

from semipy.decisions.divergence import observe_pure
from semipy.decisions.cluster import UNRUNNABLE

_SKIP = """
def avg(rows):
    vals = [r['cover'] for r in rows if r['cover'] is not None]
    return round(sum(vals) / len(vals), 4) if vals else None
"""

_ZERO = """
def avg(rows):
    vals = [(r['cover'] or 0) for r in rows]
    return round(sum(vals) / len(vals), 4) if vals else None
"""

_NULL_INPUT = [{"rows": [{"cover": 0.4}, {"cover": None}, {"cover": 0.7}]}]


def _observe(candidates, sample_rows=None):
    return observe_pure(
        candidates,
        free_variables=["rows"],
        sample_rows=sample_rows if sample_rows is not None else _NULL_INPUT,
    )


def test_skip_vs_zero_splits_into_two_branches():
    res = _observe({"A": _SKIP, "B": _ZERO})
    assert res.diverged()
    assert len(res.clusters) == 2


def test_cluster_weights_reflect_candidate_share():
    cands = {"a": _SKIP, "b": _SKIP, "c": _SKIP, "d": _ZERO, "e": _ZERO}
    res = _observe(cands)
    weights = sorted((c.weight for c in res.clusters), reverse=True)
    assert weights == [0.6, 0.4]
    heavy = res.clusters[0]
    assert set(heavy.candidate_ids) == {"a", "b", "c"}


def test_float_jitter_collapses_to_one_branch():
    a = "def f(rows):\n    return 0.4\n"
    b = "def f(rows):\n    return 0.400000000001\n"
    res = _observe({"a": a, "b": b})
    assert not res.diverged()
    assert len(res.clusters) == 1


def test_dict_key_ordering_is_noise():
    a = "def f(rows):\n    return {'x': 1, 'y': 2}\n"
    b = "def f(rows):\n    return {'y': 2, 'x': 1}\n"
    res = _observe({"a": a, "b": b})
    assert len(res.clusters) == 1


def test_raising_candidate_is_its_own_cluster_not_dropped():
    good = _SKIP
    bad = "def avg(rows):\n    return 1 / 0\n"
    res = _observe({"good": good, "bad": bad})
    assert res.n_candidates == 2
    assert len(res.clusters) == 2
    bad_cluster = next(c for c in res.clusters if "bad" in c.candidate_ids)
    assert bad_cluster.signature[0].startswith("error:")


def test_unbuildable_candidate_is_unrunnable_cluster():
    res = _observe({"x": "this is not python def"})
    assert res.runs["x"].signature == (UNRUNNABLE,)


def test_empty_input_produces_single_branch():
    res = _observe({"a": _SKIP, "b": _ZERO}, sample_rows=[{"rows": []}])
    # Both return None on empty input -> agree.
    assert not res.diverged()
    assert len(res.clusters) == 1


def test_agreement_when_no_null_present():
    no_null = [{"rows": [{"cover": 0.4}, {"cover": 0.6}]}]
    res = _observe({"a": _SKIP, "b": _ZERO}, sample_rows=no_null)
    # Without a null germ in the input, skip and zero behave identically.
    assert not res.diverged()
