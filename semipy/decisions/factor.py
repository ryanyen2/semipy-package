"""Factor a multi-axis dict divergence into independent decisions (F2).

Clustering keys on the *whole* output value (``cluster.signature_for_record``),
so two candidates that differ on any feature land in different branches. When
candidates differ along more than one independent axis at once -- e.g. one picks
a different output *schema* (``first``/``last`` vs ``first_name``/``last_name``)
while others differ on a *value* (how much of the name is the surname) -- a single
``Decision`` conflates them into one fork where some branches are off-topic
("semantic duplication").

This module separates, for dict-shaped outputs, the **output-shape axis** (which
keys are present) from the **value axis** within the dominant shape, so each
surfaced decision is about one thing. It is deliberately conservative: it factors
only when every cluster's representative output is a dict, and otherwise returns
``None`` so the caller keeps the single-decision behavior. Naming each value axis
across differing key sets would need semantic key alignment (is ``last`` the same
field as ``last_name``?), which execution alone cannot decide; separating the
shape axis first removes the conflation without inventing that alignment.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from semipy.decisions.cluster import Cluster
from semipy.decisions.divergence import DivergenceResult


@dataclass
class FactorPlan:
    """One factored axis: the clusters that back it and how to label it."""

    kind: str  # "shape" | "value"
    axis_default: str  # deterministic axis label (LLM may refine value axes)
    clusters: list[Cluster]
    deterministic_fates: Optional[list[str]] = None  # set for the shape axis


def _record_value(divergence: DivergenceResult, cluster: Cluster) -> Any:
    """The representative output of a cluster as a Python object, or a sentinel."""
    run = divergence.runs[cluster.representative_id]
    if not run.records:
        return None
    rec = run.records[0]
    if rec.get("error"):
        return None
    raw = rec.get("json")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _keyset_label(keys: tuple[str, ...]) -> str:
    return "/".join(keys) + " keys"


def factor_decisions(divergence: DivergenceResult) -> Optional[list[FactorPlan]]:
    """Split a dict divergence into a shape axis + a value axis, or ``None``.

    Returns ``None`` (keep single-decision behavior) unless every cluster's output
    is a dict and the split actually removes a conflation -- i.e. the key sets
    differ AND, within the dominant key set, candidates still diverge. A pure
    value divergence (all clusters share one key set) is left to the single
    decision, which already labels it as one axis.
    """
    if divergence.mode != "pure" or len(divergence.clusters) < 2:
        return None

    values: dict[str, dict[str, Any]] = {}
    for c in divergence.clusters:
        v = _record_value(divergence, c)
        if not isinstance(v, dict):
            return None  # not all dict outputs -> do not factor
        values[c.representative_id] = v

    keyset_of: dict[str, tuple[str, ...]] = {
        rid: tuple(sorted(str(k) for k in v.keys())) for rid, v in values.items()
    }
    distinct_keysets = {keyset_of[c.representative_id] for c in divergence.clusters}
    if len(distinct_keysets) < 2:
        return None  # single schema -> the single decision already names this axis

    # Shape axis: group clusters by key set, weight by candidate count.
    by_keyset: dict[tuple[str, ...], list[Cluster]] = {}
    for c in divergence.clusters:
        by_keyset.setdefault(keyset_of[c.representative_id], []).append(c)

    def _count(clusters: list[Cluster]) -> int:
        return sum(len(c.candidate_ids) for c in clusters)

    dominant = max(by_keyset, key=lambda ks: _count(by_keyset[ks]))

    # One synthetic cluster per key set for the shape decision: merge member
    # clusters so the shape branch weight reflects all candidates of that schema.
    shape_clusters: list[Cluster] = []
    fates: list[str] = []
    total = sum(_count(cs) for cs in by_keyset.values())
    for ks, clusters in sorted(by_keyset.items(), key=lambda kv: (-_count(kv[1]), kv[0])):
        members = tuple(sorted(cid for c in clusters for cid in c.candidate_ids))
        shape_clusters.append(
            Cluster(
                branch_id=f"shape:{'/'.join(ks)}",
                candidate_ids=members,
                signature=ks,
                representative_id=clusters[0].representative_id,
                weight=len(members) / total if total else 0.0,
            )
        )
        fates.append(_keyset_label(ks))

    plans = [
        FactorPlan(
            kind="shape",
            axis_default="output shape",
            clusters=shape_clusters,
            deterministic_fates=fates,
        )
    ]

    # Value axis: within the dominant schema, do candidates still diverge?
    dom_clusters = by_keyset[dominant]
    if len(dom_clusters) > 1:
        # Re-weight the dominant-schema clusters relative to that subset.
        dom_total = _count(dom_clusters)
        reweighted = [
            Cluster(
                branch_id=c.branch_id,
                candidate_ids=c.candidate_ids,
                signature=c.signature,
                representative_id=c.representative_id,
                weight=len(c.candidate_ids) / dom_total if dom_total else 0.0,
            )
            for c in sorted(dom_clusters, key=lambda c: (-len(c.candidate_ids), c.branch_id))
        ]
        plans.append(
            FactorPlan(kind="value", axis_default="output value", clusters=reweighted)
        )
    return plans
