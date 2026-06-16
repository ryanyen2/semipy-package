"""Divergence observation (U3 pure, U4 effectful).

Run N candidate implementations over shared inputs and cluster them by observed
behavior. This is the deterministic grounding for every downstream step: the
clusters are the branches, the cluster sizes are the weights, and nothing the
classifier (U6) labels can exist without a cluster here to back it.

Two execution modes cover the use-case domains:

- ``observe_pure`` -- return-value capture for pure/deterministic slots
  (parsing, in-memory transforms). Reuses the contract batch-gist primitive.
- ``observe_effectful`` -- reified ``EffectScript`` capture for effectful slots
  (DB, server/client, webscraping), diffed by *intended effects* with no real
  mutation (U4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.agents.decision import _run_batch_gist
from semipy.contract.runner import _build_contract_gist
from semipy.decisions.cluster import (
    UNRUNNABLE,
    Cluster,
    cluster_signatures,
    signature_for_run,
)


@dataclass
class CandidateRun:
    """One candidate's observed behavior over the shared inputs."""

    candidate_id: str
    source: str
    records: list[dict[str, Any]] = field(default_factory=list)
    signature: tuple[str, ...] = (UNRUNNABLE,)
    error: Optional[str] = None


@dataclass
class DivergenceResult:
    """Clusters plus the underlying runs, ready for classification."""

    clusters: list[Cluster]
    runs: dict[str, CandidateRun]
    mode: str  # "pure" | "effectful"

    @property
    def n_candidates(self) -> int:
        return len(self.runs)

    def diverged(self) -> bool:
        """True when candidates split into more than one behavioral branch."""
        return len(self.clusters) > 1

    def representative_run(self, cluster: Cluster) -> CandidateRun:
        return self.runs[cluster.representative_id]


# ---------------------------------------------------------------------------
# Pure slots
# ---------------------------------------------------------------------------


def observe_pure(
    candidates: dict[str, str],
    *,
    free_variables: list[str],
    sample_rows: list[dict[str, Any]],
    output_names: Optional[list[str]] = None,
    scaffold_source: Optional[str] = None,
    timeout: int = 15,
) -> DivergenceResult:
    """Execute each candidate over ``sample_rows`` and cluster by return value.

    ``sample_rows`` is a list of dicts keyed by free-variable name (the same row
    shape the contract runner uses). A candidate whose gist cannot run is its own
    ``UNRUNNABLE`` cluster, never silently dropped.
    """
    runs: dict[str, CandidateRun] = {}
    for cid, source in candidates.items():
        records: list[dict[str, Any]] = []
        error: Optional[str] = None
        gist = _build_contract_gist(
            implementation_source=source,
            free_variables=list(free_variables),
            sample_rows=sample_rows,
            scaffold_source=scaffold_source,
            output_names=list(output_names or []),
        )
        if gist:
            recs = _run_batch_gist(gist, timeout=timeout)
            if len(recs) == len(sample_rows):
                records = recs
            else:
                error = "candidate gist did not run over all inputs"
        else:
            error = "could not build candidate gist (no function found)"
        signature = signature_for_run(records) if records else (UNRUNNABLE,)
        runs[cid] = CandidateRun(
            candidate_id=cid,
            source=source,
            records=records,
            signature=signature,
            error=error,
        )

    clusters = cluster_signatures({cid: r.signature for cid, r in runs.items()})
    return DivergenceResult(clusters=clusters, runs=runs, mode="pure")


# ---------------------------------------------------------------------------
# Effectful slots
# ---------------------------------------------------------------------------


def _effect_signature(script: Any) -> tuple[str, ...]:
    """A structural signature over a reified EffectScript: per effect, the op,
    target, and the sorted payload/selector key shape -- not payload *values*,
    so two candidates that write the same shape to the same target cluster
    together even with different scratch code."""
    effects = list(getattr(script, "effects", []) or [])
    parts: list[str] = []
    for eff in effects:
        op = getattr(eff, "op", "?")
        target = getattr(eff, "target", "?")
        payload = getattr(eff, "payload", None)
        selector = getattr(eff, "selector", None)
        pkeys = ",".join(sorted(str(k) for k in payload)) if isinstance(payload, dict) else type(payload).__name__
        skeys = ",".join(sorted(str(k) for k in selector)) if isinstance(selector, dict) else type(selector).__name__
        parts.append(f"{op}@{target}|p:{pkeys}|s:{skeys}")
    return tuple(parts) if parts else ("__no_effects__",)


def observe_effectful(
    candidates: dict[str, str],
    *,
    free_variables: list[str],
    runtime_values: dict[str, Any],
) -> DivergenceResult:
    """Cluster effectful candidates by their reified ``EffectScript``.

    Each candidate runs through ``effects.shadow.run_effectful_source`` -- bound
    to a fresh shadow world, so no real DB/file/API mutation occurs -- and is
    clustered by the structural signature of the effects it intended to perform.
    """
    from semipy.effects.shadow import run_effectful_source

    runs: dict[str, CandidateRun] = {}
    for cid, source in candidates.items():
        script, _world, err = run_effectful_source(
            source,
            free_variables=list(free_variables),
            runtime_values=runtime_values,
        )
        if err is not None or script is None:
            signature: tuple[str, ...] = (UNRUNNABLE,)
            records: list[dict[str, Any]] = [{"error": err or "no script"}]
        else:
            signature = _effect_signature(script)
            records = [{"effects": _effect_signature(script)}]
        runs[cid] = CandidateRun(
            candidate_id=cid,
            source=source,
            records=records,
            signature=signature,
            error=err,
        )

    clusters = cluster_signatures({cid: r.signature for cid, r in runs.items()})
    return DivergenceResult(clusters=clusters, runs=runs, mode="effectful")
