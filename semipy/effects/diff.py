"""Artifact-state effect-diff: is a regenerated impl more destructive than its parent?

The Stage 1 gate catches *absolute* dangers (a selectorless wipe, an irreversible
effect). This module catches *relative* ones: an ADAPT that silently escalates the
blast radius -- e.g. the parent updated one row, the regenerated impl now deletes
many. It runs the parent and the candidate over the same input against fresh
shadows and compares the resulting :class:`StateDelta`s (ground truth of what
changed), reusing the backend's ``snapshot``/``diff`` -- no parallel comparator.

The rule is deliberately conservative (few false positives on legitimate
refinements): a regression is flagged only when the candidate **removes more
records than the parent** (destructive escalation) or **affects materially more**
(more than 2x and a growth larger than the configured bound). Adding a benign
history-append the parent lacked is not a regression.

Once the ledger (Stage 4) records previously-applied inputs, those inputs feed
this same machinery to re-check regressions on inputs the parent handled, not only
the triggering one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.effects.backends import StateDelta, resolve_backend
from semipy.effects.models import EffectScript


@dataclass
class TargetDiff:
    target: str
    parent_affected: int
    new_affected: int
    parent_removed: int
    new_removed: int


@dataclass
class EffectStateDiff:
    per_target: list[TargetDiff] = field(default_factory=list)
    regression: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.regression:
            return ""
        return "Blast-radius regression vs the previous implementation: " + "; ".join(self.reasons)


def state_delta_for_script(script: EffectScript) -> dict[str, StateDelta]:
    """Replay a script's mutating effects on fresh shadows and return per-target deltas.

    Reads do not change state, so only mutating effects are replayed. Shadows are
    always discarded -- this is pure measurement.
    """
    by_target: dict[str, list] = {}
    for e in script.mutating():
        by_target.setdefault(e.target, []).append(e)
    deltas: dict[str, StateDelta] = {}
    for target, effs in by_target.items():
        try:
            be = resolve_backend(target)
            sh = be.open_shadow(target)
        except Exception:
            continue
        try:
            before = be.snapshot(sh)
            for e in effs:
                be.apply(sh, e)
            after = be.snapshot(sh)
            deltas[target] = be.diff(before, after)
        finally:
            try:
                be.discard(sh)
            except Exception:
                pass
    return deltas


def _run_to_script(
    source: str, *, free_variables: list[str], runtime_values: dict[str, Any],
    namespace: Optional[dict[str, Any]], provenance: Optional[dict[str, Any]],
) -> Optional[EffectScript]:
    from semipy.effects.shadow import run_effectful_source

    script, world, err = run_effectful_source(
        source, free_variables=free_variables, runtime_values=runtime_values,
        namespace=namespace, provenance=provenance,
    )
    world.discard_all()
    return None if err is not None else script


def compute_effect_state_diff(
    *,
    parent_source: Optional[str],
    new_script: EffectScript,
    free_variables: list[str],
    runtime_values: dict[str, Any],
    namespace: Optional[dict[str, Any]] = None,
    provenance: Optional[dict[str, Any]] = None,
    default_blast_radius: int = 1,
) -> EffectStateDiff:
    """Compare the candidate's artifact-state delta against the parent's on one input."""
    diff = EffectStateDiff()
    if not parent_source:
        return diff  # fresh GENERATE: nothing to regress against

    new_deltas = state_delta_for_script(new_script)
    parent_script = _run_to_script(
        parent_source, free_variables=free_variables, runtime_values=runtime_values,
        namespace=namespace, provenance=provenance,
    )
    parent_deltas = state_delta_for_script(parent_script) if parent_script is not None else {}

    for target in sorted(set(new_deltas) | set(parent_deltas)):
        nd = new_deltas.get(target)
        pd = parent_deltas.get(target)
        n_aff = nd.affected_count() if nd else 0
        p_aff = pd.affected_count() if pd else 0
        n_rem = len(nd.removed) if nd else 0
        p_rem = len(pd.removed) if pd else 0
        diff.per_target.append(TargetDiff(target, p_aff, n_aff, p_rem, n_rem))
        if n_rem > p_rem:
            diff.regression = True
            diff.reasons.append(
                f"{target}: removes {n_rem} record(s) vs the parent's {p_rem} (more destructive)"
            )
        elif n_aff > 2 * p_aff and (n_aff - p_aff) > default_blast_radius:
            diff.regression = True
            diff.reasons.append(
                f"{target}: affects {n_aff} record(s) vs the parent's {p_aff} (materially larger)"
            )
    return diff
