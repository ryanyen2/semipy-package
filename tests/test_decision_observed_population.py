"""D2 regression: observing divergence over the recorded input population.

The pure decision path used to feed ``observe_pure`` a single row (the current
runtime invocation). Two candidates that happen to agree on that one input are
then merged into one behavioral branch even when they diverge on other inputs --
an under-sampling artifact, not a real agreement.

These tests pin that on the real cached apache slot (5 candidates, 19 recorded
observations): over one row, c2 and c3 merge into a 4th branch; over the recorded
population they separate into 5 distinct behaviors. The fix in
``slot_resolver`` builds the sample rows from ``input_observation_samples``
(decoupled across free variables) instead of the single runtime row.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from semipy.decisions.divergence import observe_pure

_PORTAL = (
    Path(__file__).resolve().parent.parent
    / "examples" / ".semiformal" / "855628c46486f6f5.portal.json"
)
_SLOT_ID = "a85d013a2ddaa861"
_FREE_VARS = ["body", "lower", "text"]


def _load_slot():
    if not _PORTAL.exists():
        pytest.skip(f"apache portal fixture not present at {_PORTAL}")
    slot = json.loads(_PORTAL.read_text())["slots"][_SLOT_ID]
    return slot["decision_set"]["candidates"], slot["input_observation_samples"]


def _cluster_of(clusters, cid):
    return next(c for c in clusters if cid in c.candidate_ids)


def test_single_observed_row_merges_distinct_behaviors():
    """The old single-row path pools c2 and c3 into one branch (the bug)."""
    candidates, obs = _load_slot()
    row0 = {v: obs[v][0] for v in _FREE_VARS}
    res = observe_pure(candidates, free_variables=_FREE_VARS, sample_rows=[row0])
    assert len(res.clusters) == 4
    assert _cluster_of(res.clusters, "c2") is _cluster_of(res.clusters, "c3")


def test_observed_population_separates_merged_behaviors():
    """Feeding the recorded population separates all five behaviors (the fix)."""
    candidates, obs = _load_slot()
    # Decoupled one-variable-at-a-time from the base row, mirroring
    # slot_observations._reuse_verify_sample_inputs: hold the invocation fixed and
    # substitute each recorded observation for one free variable at a time.
    base = {v: obs[v][0] for v in _FREE_VARS}
    rows = [dict(base)]
    for name in _FREE_VARS:
        for value in obs[name]:
            row = dict(base)
            row[name] = value
            rows.append(row)

    res = observe_pure(candidates, free_variables=_FREE_VARS, sample_rows=rows)
    assert len(res.clusters) == 5
    assert _cluster_of(res.clusters, "c2") is not _cluster_of(res.clusters, "c3")
