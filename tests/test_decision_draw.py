"""U2: adaptive multi-candidate draw and resolve."""
from __future__ import annotations

from semipy.decisions.draw import resolve_with_decisions

_SKIP = "def avg(rows):\n    v=[r['c'] for r in rows if r['c'] is not None]\n    return round(sum(v)/len(v),4) if v else None\n"
_ZERO = "def avg(rows):\n    v=[(r['c'] or 0) for r in rows]\n    return round(sum(v)/len(v),4) if v else None\n"
_ROWS = [{"rows": [{"c": 0.4}, {"c": None}, {"c": 0.7}]}]


def _alternating(i):
    return _SKIP if i % 2 == 0 else _ZERO


def _always_skip(i):
    return _SKIP


def test_agreement_returns_single_head_no_decisions():
    out = resolve_with_decisions(
        generate_candidate=_always_skip,
        free_variables=["rows"],
        sample_rows=_ROWS,
        use_llm=False,
    )
    assert not out.diverged
    assert not out.has_decisions
    assert out.head_source == _SKIP


def test_divergence_escalates_and_surfaces_decision():
    out = resolve_with_decisions(
        generate_candidate=_alternating,
        free_variables=["rows"],
        sample_rows=_ROWS,
        initial_candidates=3,
        max_candidates=5,
        slot_id="slot1",
        use_llm=False,
    )
    assert out.diverged
    assert out.has_decisions
    # Escalated to the max of 5 candidates, all sources retained.
    assert len(out.decision_set.candidates) == 5
    assert len(out.decision_set.decisions) == 1
    assert len(out.decision_set.decisions[0].branches) == 2


def test_head_comes_from_heaviest_cluster():
    # Indices 0,2,4 -> skip (3); 1,3 -> zero (2). Head should be a skip candidate.
    out = resolve_with_decisions(
        generate_candidate=_alternating,
        free_variables=["rows"],
        sample_rows=_ROWS,
        use_llm=False,
    )
    assert out.head_source == _SKIP


def test_germ_detected_as_null_via_discriminating_search():
    # The sample input has a null, so the surfaced germ should be the null germ.
    out = resolve_with_decisions(
        generate_candidate=_alternating,
        free_variables=["rows"],
        sample_rows=[{"rows": [{"c": 0.4}, {"c": 0.6}]}],  # no null in base
        use_llm=False,
    )
    # Even without a null in the base sample, escalation + discriminating search
    # exposes the null fork.
    assert out.diverged
    assert out.decision_set.decisions[0].germ == "null"


def test_all_unrunnable_does_not_crash():
    out = resolve_with_decisions(
        generate_candidate=lambda i: "not python",
        free_variables=["rows"],
        sample_rows=_ROWS,
        use_llm=False,
    )
    assert not out.diverged
    assert not out.has_decisions
