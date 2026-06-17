"""U6: decision classifier role (deterministic / no-key path)."""
from __future__ import annotations

from semipy.decisions.divergence import observe_pure
from semipy.orchestration.roles.decision_classifier import (
    classify_divergence,
    rank_decisions,
)

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

_KEYS_OMIT = "def f(rows):\n    return {'s1': 0.4}\n"
_KEYS_NAN = "def f(rows):\n    return {'s1': 0.4, 's3': None}\n"


def _diverge(cands, rows=None):
    return observe_pure(cands, free_variables=["rows"], sample_rows=rows or _NULL_INPUT)


def test_no_key_path_yields_unlabeled_decision_with_branches():
    div = _diverge({"a": _SKIP, "b": _ZERO})
    decisions = classify_divergence(div, germ="null", use_llm=False)
    assert len(decisions) == 1
    d = decisions[0]
    assert not d.labeled
    assert d.germ == "null"
    assert len(d.branches) == 2
    # Fate labels are output-derived in the no-key view.
    assert all(b.fate_label for b in d.branches)


def test_single_cluster_yields_no_decision():
    same = _diverge({"a": _SKIP, "b": _SKIP})
    assert classify_divergence(same, use_llm=False) == []


def test_branches_carry_candidate_ids_and_weights():
    div = _diverge({"a": _SKIP, "b": _SKIP, "c": _ZERO})
    d = classify_divergence(div, use_llm=False)[0]
    weights = sorted(b.weight for b in d.branches)
    assert weights == [round(1 / 3, 4), round(2 / 3, 4)]
    all_ids = {cid for b in d.branches for cid in b.candidate_ids}
    assert all_ids == {"a", "b", "c"}


def test_structural_outranks_numeric():
    structural = classify_divergence(
        _diverge({"a": _KEYS_OMIT, "b": _KEYS_NAN}), germ="missing-key", use_llm=False
    )[0]
    numeric = classify_divergence(
        _diverge({"a": _SKIP, "b": _ZERO}), germ="null", use_llm=False
    )[0]
    assert structural.consequence_kind == "structural"
    assert numeric.consequence_kind == "numeric"
    ranked = rank_decisions([numeric, structural])
    assert ranked[0] is structural


def test_decision_id_is_deterministic():
    d1 = classify_divergence(_diverge({"a": _SKIP, "b": _ZERO}), use_llm=False)[0]
    d2 = classify_divergence(_diverge({"a": _SKIP, "b": _ZERO}), use_llm=False)[0]
    assert d1.decision_id == d2.decision_id
