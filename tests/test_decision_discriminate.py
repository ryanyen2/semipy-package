"""U5: discriminating-input search."""
from __future__ import annotations

from semipy.decisions import germs
from semipy.decisions.discriminate import search_discriminating_inputs

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

# A sample with NO nulls -- skip and zero agree here; the fork is hidden.
_NO_NULL = [{"rows": [{"cover": 0.4}, {"cover": 0.6}]}]


def test_finds_hidden_null_fork():
    res = search_discriminating_inputs(
        {"A": _SKIP, "B": _ZERO},
        free_variables=["rows"],
        base_rows=_NO_NULL,
    )
    assert res.base_clusters == 1
    assert res.found
    assert res.best_clusters >= 2
    assert res.germ == germs.NULL


def test_minimized_input_is_small_and_still_splits():
    res = search_discriminating_inputs(
        {"A": _SKIP, "B": _ZERO},
        free_variables=["rows"],
        base_rows=_NO_NULL,
    )
    assert res.minimized_input is not None
    minimized_rows = res.minimized_input["rows"]
    # The smallest splitting case is a single null reading.
    assert len(minimized_rows) == 1
    assert minimized_rows[0]["cover"] is None


def test_no_fork_when_candidates_identical():
    same = _SKIP
    res = search_discriminating_inputs(
        {"a": same, "b": same},
        free_variables=["rows"],
        base_rows=_NO_NULL,
    )
    assert not res.found
    assert res.minimized_input is None
    assert res.tried > 0


# Two candidates that agree while a == b (as they do in every recorded input)
# but diverge once that correlation is broken. The primary variable (mode) is
# inert, so a primary-only search never sees the fork; the search must probe the
# non-primary variables and decouple them from their observed correlation.
_USE_A = "def f(mode, a, b):\n    return a\n"
_USE_B = "def f(mode, a, b):\n    return b\n"


def test_search_decouples_correlated_nonprimary_variables():
    res = search_discriminating_inputs(
        {"A": _USE_A, "B": _USE_B},
        free_variables=["mode", "a", "b"],
        base_rows=[{"mode": "x", "a": "hi", "b": "hi"}],
    )
    assert res.base_clusters == 1  # a == b on the observed input: they agree
    assert res.found
    assert res.best_clusters == 2
    assert res.variable in ("a", "b")
