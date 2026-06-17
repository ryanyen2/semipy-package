"""Cross-domain hardening (U11): determinism, cost guard, decision-structure.

Observed-output divergence is clean for pure and effectful slots. The hard
domains are nondeterministic (scraping) and expensive/high-variance (model
training, visualization). This module makes divergence observation *honest*
there rather than faking coverage:

- **Seeding** -- pin RNG state so repeated candidate runs are reproducible, a
  precondition for clustering nondeterministic slots.
- **Cost guard** -- bound the wall-clock spent observing one slot so an expensive
  candidate cannot hang resolution; over-budget yields a flagged partial result.
- **Decision structure** -- for model training / visualization, cluster on the
  *decision-bearing* structure (which feature/split/chart-type was chosen) and
  collapse the volatile numeric artifact (trained weights, rendered pixels), so
  two candidates that made the same choice cluster together despite differing
  floats.
- **Comparability** -- when a slot's output is non-reproducible even when seeded
  (e.g. an object repr with a memory address), report "no comparable signal"
  rather than surfacing noise as a decision.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from semipy.decisions.cluster import UNRUNNABLE, Cluster, cluster_signatures
from semipy.decisions.divergence import DivergenceResult, observe_pure


# ---------------------------------------------------------------------------
# Determinism (seeding)
# ---------------------------------------------------------------------------


def seed_preamble(seed: int = 0) -> str:
    """Module-level preamble that pins the common RNG sources for reproducibility."""
    return (
        "import os as _os, random as _random\n"
        f"_os.environ.setdefault('PYTHONHASHSEED', '{seed}')\n"
        f"_random.seed({seed})\n"
        "try:\n"
        "    import numpy as _np\n"
        f"    _np.random.seed({seed})\n"
        "except Exception:\n"
        "    pass\n"
    )


def seeded_candidates(candidates: dict[str, str], seed: int = 0) -> dict[str, str]:
    """Prepend the seed preamble to each candidate so its gist runs reproducibly."""
    pre = seed_preamble(seed)
    return {cid: pre + "\n" + src for cid, src in candidates.items()}


def observe_seeded(
    candidates: dict[str, str],
    *,
    free_variables: list[str],
    sample_rows: list[dict[str, Any]],
    output_names: Optional[list[str]] = None,
    seed: int = 0,
    timeout: int = 15,
) -> DivergenceResult:
    """Observe pure divergence with RNG seeded, so nondeterministic slots cluster."""
    return observe_pure(
        seeded_candidates(candidates, seed),
        free_variables=free_variables,
        sample_rows=sample_rows,
        output_names=output_names,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Cost guard
# ---------------------------------------------------------------------------


@dataclass
class CostGuard:
    """Wall-clock budget for observing one slot's divergence."""

    budget_s: float
    _start: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    @property
    def exceeded(self) -> bool:
        return self.elapsed > self.budget_s


def collect_within_budget(
    thunks: list[Callable[[], Any]],
    guard: CostGuard,
) -> tuple[list[Any], bool]:
    """Run ``thunks`` until the budget is exceeded. Returns (results, cost_limited).

    Never hangs: once the guard is exceeded it stops and reports the partial set,
    flagged ``cost_limited=True``, rather than running the remaining work.
    """
    out: list[Any] = []
    for t in thunks:
        if guard.exceeded:
            return out, True
        out.append(t())
    return out, False


# ---------------------------------------------------------------------------
# Decision structure (model training / visualization)
# ---------------------------------------------------------------------------


def decision_structure(obj: Any) -> Any:
    """Reduce a value to its decision-bearing structure.

    Categorical choices (strings, ints, bools, None) are kept by value -- they are
    the decision (which feature, which chart type). Volatile numeric artifacts
    (floats, all-numeric vectors) collapse to a type+shape token, so two candidates
    that chose the same structure cluster together despite different trained values.
    """
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return "<float>"
    if isinstance(obj, dict):
        return {str(k): decision_structure(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, list):
        if obj and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in obj):
            return f"<numeric[{len(obj)}]>"
        return [decision_structure(v) for v in obj]
    return obj


def _structural_signature(records: list[dict[str, Any]]) -> tuple[str, ...]:
    parts: list[str] = []
    for rec in records:
        if rec.get("error"):
            parts.append("error:" + str(rec["error"]).split(":", 1)[0])
            continue
        raw = rec.get("json")
        if raw is not None:
            try:
                parts.append("st:" + json.dumps(decision_structure(json.loads(raw)), sort_keys=True))
                continue
            except Exception:
                pass
        parts.append(f"shape:{rec.get('type', '?')}:{rec.get('shape', '?')}")
    return tuple(parts) if parts else (UNRUNNABLE,)


def cluster_by_decision_structure(divergence: DivergenceResult) -> list[Cluster]:
    """Re-cluster a divergence on decision structure (ignoring volatile values)."""
    sigs = {cid: _structural_signature(run.records) for cid, run in divergence.runs.items()}
    return cluster_signatures(sigs)


# ---------------------------------------------------------------------------
# Comparability ("no comparable signal")
# ---------------------------------------------------------------------------


@dataclass
class ComparabilityReport:
    comparable: bool
    reason: str = ""


def is_reproducible(
    candidate_source: str,
    *,
    free_variables: list[str],
    sample_rows: list[dict[str, Any]],
    output_names: Optional[list[str]] = None,
    seed: int = 0,
    timeout: int = 15,
) -> bool:
    """True when a candidate's seeded output is stable across two runs."""
    seeded = seeded_candidates({"c": candidate_source}, seed)

    def _sig() -> tuple[str, ...]:
        res = observe_pure(
            seeded,
            free_variables=free_variables,
            sample_rows=sample_rows,
            output_names=output_names,
            timeout=timeout,
        )
        return res.runs["c"].signature

    s1 = _sig()
    s2 = _sig()
    return s1 == s2 and s1 != (UNRUNNABLE,)


def assess_comparability(
    candidates: dict[str, str],
    *,
    free_variables: list[str],
    sample_rows: list[dict[str, Any]],
    output_names: Optional[list[str]] = None,
    seed: int = 0,
    timeout: int = 15,
) -> ComparabilityReport:
    """Report whether divergence on these candidates carries a comparable signal.

    When even a seeded candidate is non-reproducible (e.g. an output repr with a
    memory address), clustering would surface noise -- so report no comparable
    signal honestly instead.
    """
    for cid, src in candidates.items():
        if not is_reproducible(
            src,
            free_variables=free_variables,
            sample_rows=sample_rows,
            output_names=output_names,
            seed=seed,
            timeout=timeout,
        ):
            return ComparabilityReport(
                comparable=False,
                reason=f"candidate {cid} output is non-reproducible even when seeded",
            )
    return ComparabilityReport(comparable=True)
