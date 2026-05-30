"""Effect tracing: the traced effect of a change is the behavior diff between the
parent implementation and the new one over the union of contract inputs.

Run BOTH implementations over the same inputs; for each input *pattern* whose
output changed, record old -> new and classify it as intended (the change we
wanted) or unintended (a regression). A change is intended when it lands on the
triggering input, or when the parent was already wrong there (errored or violated
its own case) — every other changed output is a regression. The diff is stored on
the commit as the real record of *what this change did*, replacing the generic
commit message.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from semipy.contract.fingerprint import structural_input_fingerprint


@dataclass
class EffectDiffEntry:
    input_fingerprint: str
    input_repr: str
    old_repr: str
    new_repr: str
    intended: bool


@dataclass
class ChangeRecord:
    """The WHY (reason, trigger) and the traced EFFECT (behavior diff) of a commit."""

    reason: str = ""
    triggering_input_fingerprint: str = ""
    decision: str = ""
    parent_commit_id: str = ""
    effect_diff: list[EffectDiffEntry] = field(default_factory=list)
    unintended_count: int = 0
    n_compared: int = 0

    def summary(self) -> str:
        intended = len(self.effect_diff) - self.unintended_count
        return (
            f"{self.decision or 'change'}: {intended} intended, "
            f"{self.unintended_count} unintended over {self.n_compared} input pattern(s)"
        )


def change_record_to_dict(cr: ChangeRecord) -> dict[str, Any]:
    return {
        "reason": cr.reason,
        "triggering_input_fingerprint": cr.triggering_input_fingerprint,
        "decision": cr.decision,
        "parent_commit_id": cr.parent_commit_id,
        "unintended_count": int(cr.unintended_count),
        "n_compared": int(cr.n_compared),
        "effect_diff": [
            {
                "input_fingerprint": e.input_fingerprint,
                "input_repr": e.input_repr,
                "old_repr": e.old_repr,
                "new_repr": e.new_repr,
                "intended": bool(e.intended),
            }
            for e in cr.effect_diff
        ],
    }


def change_record_from_dict(d: dict[str, Any] | None) -> ChangeRecord:
    if not isinstance(d, dict):
        return ChangeRecord()
    valid = {f.name for f in fields(ChangeRecord)}
    kwargs = {k: v for k, v in d.items() if k in valid and k != "effect_diff"}
    entries = []
    for e in d.get("effect_diff", []) or []:
        if isinstance(e, dict):
            entries.append(
                EffectDiffEntry(
                    input_fingerprint=e.get("input_fingerprint", ""),
                    input_repr=e.get("input_repr", ""),
                    old_repr=e.get("old_repr", ""),
                    new_repr=e.get("new_repr", ""),
                    intended=bool(e.get("intended", False)),
                )
            )
    return ChangeRecord(effect_diff=entries, **kwargs)


def compute_effect_diff(
    *,
    parent_source: str | None,
    new_source: str,
    slot_spec: Any,
    cases: list[Any],
    triggering_fp: str,
    scaffold_source: str | None,
    reason: str = "",
    decision: str = "",
    parent_commit_id: str = "",
) -> ChangeRecord:
    """Diff parent vs new over the union of (deduplicated) contract-case inputs."""
    from semipy.agents.decision import _run_batch_gist
    from semipy.contract.runner import _build_contract_gist, _eval_single, _row_for_input

    base = ChangeRecord(
        reason=reason,
        triggering_input_fingerprint=triggering_fp,
        decision=decision,
        parent_commit_id=parent_commit_id,
    )
    free_vars = list(slot_spec.free_variables)
    if not parent_source or not cases:
        return base

    rows: list[dict[str, Any]] = []
    paired: list[Any] = []
    seen: set[str] = set()
    for case in cases:
        row = _row_for_input(case.input_sample, free_vars)
        if row is None:
            continue
        fp = case.input_fingerprint or structural_input_fingerprint(
            case.input_sample, free_variables=free_vars
        )
        if fp in seen:
            continue
        seen.add(fp)
        rows.append(row)
        paired.append(case)
    if not rows:
        return base

    output_names = list(getattr(slot_spec, "output_names", None) or [])
    parent_gist = _build_contract_gist(
        implementation_source=parent_source,
        free_variables=free_vars,
        sample_rows=rows,
        scaffold_source=scaffold_source,
        output_names=output_names,
    )
    new_gist = _build_contract_gist(
        implementation_source=new_source,
        free_variables=free_vars,
        sample_rows=rows,
        scaffold_source=scaffold_source,
        output_names=output_names,
    )
    precs = _run_batch_gist(parent_gist)
    nrecs = _run_batch_gist(new_gist)
    if len(precs) != len(rows) or len(nrecs) != len(rows):
        return base

    diff: list[EffectDiffEntry] = []
    unintended = 0
    for i, case in enumerate(paired):
        prec, nrec = precs[i], nrecs[i]
        old_key = prec.get("json") if prec.get("error") is None else f"error:{prec.get('error')}"
        new_key = nrec.get("json") if nrec.get("error") is None else f"error:{nrec.get('error')}"
        if old_key == new_key:
            continue
        fp = case.input_fingerprint
        parent_was_wrong = prec.get("error") is not None
        if not parent_was_wrong and case.kind in ("example", "invariant"):
            # Parent violated its own recorded case -> changing it is intended.
            try:
                parent_was_wrong = _eval_single(case, prec) is not None
            except Exception:
                parent_was_wrong = False
        intended = (fp == triggering_fp) or parent_was_wrong
        if not intended:
            unintended += 1
        diff.append(
            EffectDiffEntry(
                input_fingerprint=fp,
                input_repr=str(case.primary_input)[:80],
                old_repr=str(prec.get("repr", old_key))[:160],
                new_repr=str(nrec.get("repr", new_key))[:160],
                intended=intended,
            )
        )

    base.effect_diff = diff
    base.unintended_count = unintended
    base.n_compared = len(rows)
    return base


def regression_summary(cr: ChangeRecord) -> str:
    """Human/LLM-facing description of unintended changes, for the ADAPT prompt."""
    regs = [e for e in cr.effect_diff if not e.intended]
    if not regs:
        return ""
    parts = [
        f"input like {e.input_repr!r}: was {e.old_repr!r}, now {e.new_repr!r}"
        for e in regs[:5]
    ]
    return (
        "The new implementation changed output for inputs it should have left unchanged. "
        "Preserve the previous output for these: " + "; ".join(parts)
    )
