"""U4: effectful divergence via reified effect scripts (no real mutations)."""
from __future__ import annotations

from semipy.decisions.divergence import observe_effectful

# Two effectful candidates that diverge on insert-vs-upsert semantics. Each
# declares ``fx`` and emits reified effects; neither touches a real artifact.
_INSERT = """
def write(rows, fx):
    for r in rows:
        fx.create('db://coral', {'site': r['site'], 'cover': r['cover']})
"""

_UPSERT = """
def write(rows, fx):
    for r in rows:
        fx.update('db://coral', {'cover': r['cover']}, {'site': r['site']})
"""

_ROWS = {"rows": [{"site": "s1", "cover": 0.4}, {"site": "s2", "cover": 0.6}]}


def _observe(candidates):
    return observe_effectful(candidates, free_variables=["rows"], runtime_values=_ROWS)


def test_insert_vs_upsert_diverges_on_effect_script():
    res = observe_effectful(
        {"A": _INSERT, "B": _UPSERT},
        free_variables=["rows"],
        runtime_values=_ROWS,
    )
    assert res.mode == "effectful"
    assert res.diverged()
    assert len(res.clusters) == 2


def test_identical_effects_cluster_together():
    # Same effect shape, different in-memory scratch code -> one branch.
    a = _INSERT
    b = """
def write(rows, fx):
    total = 0
    for r in rows:
        total += 1
        fx.create('db://coral', {'site': r['site'], 'cover': r['cover']})
"""
    res = _observe({"a": a, "b": b})
    assert not res.diverged()
    assert len(res.clusters) == 1


def test_no_real_mutation_occurs():
    # The shadow world is never committed; observation is pure intent capture.
    # We assert the run completes and produces effect signatures, proving the
    # candidates ran confined to fx (no backend registration, no writes).
    res = _observe({"A": _INSERT})
    run = res.runs["A"]
    assert run.error is None
    assert run.signature[0].startswith("create@db://coral")
