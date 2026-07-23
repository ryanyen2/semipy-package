"""Re-open a committed decision under input-population drift (Phase 5).

A branch that pools more than one candidate is only *equivalent over the N
tested inputs* -- an ∃-sample claim, never a ∀-input one. When the slot's
observation population later grows a genuinely new input pattern, the same
stored candidates may split into more behavioral classes than the committed set
recorded. That is the re-open trigger: re-cluster the stored candidate sources
over the enlarged population and, if the partition got finer, rebuild the
decisions (open) so the ``#?`` surface re-renders the fork.

Cheap and deterministic: the candidate sources are already on the DecisionSet,
so re-observation re-runs them in memory with no LLM call. Called only when the
observation fingerprint changed (see ``slot_resolver``).
"""
from __future__ import annotations

from typing import Any, Optional

from semipy.decisions.divergence import observe_pure
from semipy.decisions.model import DecisionSet


def _committed_class_count(decision_set: DecisionSet) -> int:
    """Behavioral classes the committed set represents = distinct branch signatures.

    A cluster carries one signature, so distinct signatures across all branches is
    the class count. (For a factored set several decisions partition the same
    candidates along different axes; the distinct-signature count is then a floor,
    which only makes this trigger more eager -- a conservative, never-silent choice.)
    """
    return len({tuple(b.signature) for d in decision_set.decisions for b in d.branches})


def reopen_on_input_drift(
    decision_set: DecisionSet,
    *,
    free_variables: list[str],
    sample_rows: list[dict[str, Any]],
    output_names: Optional[list[str]] = None,
    new_fingerprint: str = "",
    timeout: int = 15,
) -> bool:
    """Re-cluster the stored candidates over ``sample_rows``; re-open on a finer split.

    Records ``new_fingerprint`` on ``decision_set`` (so the same population is not
    re-observed twice) and returns ``True`` iff the enlarged population produced
    more behavioral classes than the committed set had -- in which case
    ``decision_set.decisions`` is replaced with the finer, re-opened partition.
    """
    decision_set.obs_fingerprint = new_fingerprint
    if decision_set.is_empty() or not decision_set.candidates:
        return False

    committed = _committed_class_count(decision_set)
    div = observe_pure(
        decision_set.candidates,
        free_variables=list(free_variables),
        sample_rows=sample_rows,
        output_names=list(output_names or []) or None,
        timeout=timeout,
    )
    if len(div.clusters) <= committed:
        return False

    # A previously-merged branch split. Rebuild from the finer partition, offline
    # (deterministic labels): the user re-labels by steering the re-surfaced fork.
    from semipy.orchestration.roles.decision_classifier import classify_divergence

    germ = decision_set.decisions[0].germ if decision_set.decisions else "output"
    example_in = sample_rows[0] if sample_rows else None
    new_decisions = classify_divergence(
        div, germ=germ, example_in=example_in, use_llm=False
    )
    if not new_decisions:
        return False
    decision_set.decisions = new_decisions
    return True
