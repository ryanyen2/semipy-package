"""Contract maintainer: a separate TDD/BDD pass that updates the behavioral
contract after a successful GENERATE/ADAPT.

Two layers:

1. Deterministic invariant seeding (no LLM, always runs when the contract is
   enabled). For each distinct observed input *pattern*, characterise the new
   implementation's actual behavior and persist the data-agnostic invariants that
   hold — ``non_empty`` / ``non_identity`` / ``type_match``. This permanently
   promotes the validator's transient guards into carried-forward cases, so the
   acceptance gate has something to enforce even with the LLM pass off.

2. Selective LLM pass (default off). Given the spec, the new source, concrete
   {input -> output} pairs, the change record, and the existing cases, the model
   proposes a few canonical golden-master examples, metamorphic relations from the
   fixed registry, and supersedes for example cases the change deliberately broke.
   Every proposed case is verified to actually hold on the new source before it is
   added, so the contract stays self-consistent.

The maintainer never deletes a case: superseded/quarantined rows are retained for
the audit trail ("never forget").
"""
from __future__ import annotations

import sys
from typing import Any, Literal, Optional

from pydantic import BaseModel

from semipy.agents.config import get_config
from semipy.agents.decision import _pick_diverse_samples, _run_batch_gist
from semipy.contract.access import get_contract, save_contract
from semipy.contract.change import change_record_from_dict
from semipy.contract.fingerprint import structural_input_fingerprint
from semipy.contract.models import (
    INVARIANT_NAMES,
    ContractCase,
    SlotContract,
    compute_case_id,
)
from semipy.contract.relations import relation_names
from semipy.contract.runner import _build_contract_gist, _row_for_input, run_contract
from semipy.store import load_portal, save_portal

# Builtin types whose name survives the subprocess gist and is safe to pin.
_SAFE_TYPE_NAMES = {"str", "int", "float", "bool", "list", "dict", "tuple"}
_IDENTITY_MIN_LEN = 9
_MAX_SEED_PATTERNS = 8


# ---------------------------------------------------------------------------
# LLM proposal schemas
# ---------------------------------------------------------------------------


class ProposedCase(BaseModel):
    kind: Literal["example", "invariant", "metamorphic"]
    invariant: Optional[str] = None       # one of INVARIANT_NAMES when kind=="invariant"
    relation: Optional[str] = None        # registry name when kind=="metamorphic"
    input_index: Optional[int] = None     # index into the candidate inputs provided
    reason: str = ""
    effect: str = ""


class SupersedeProposal(BaseModel):
    case_id: str
    why: str


class ContractUpdate(BaseModel):
    new_cases: list[ProposedCase] = []
    supersede: list[SupersedeProposal] = []


# ---------------------------------------------------------------------------
# Candidate inputs + behavior capture
# ---------------------------------------------------------------------------


def _candidate_rows(
    slot: Any, slot_spec: Any, runtime_values: dict[str, Any]
) -> list[dict[str, Any]]:
    """Triggering input first, then diverse harvested observations (deduped by pattern)."""
    free_vars = list(slot_spec.free_variables)
    rows: list[dict[str, Any]] = []
    seen_fp: set[str] = set()

    trigger = _row_for_input(runtime_values, free_vars)
    if trigger is not None:
        fp = structural_input_fingerprint(runtime_values, free_variables=free_vars)
        seen_fp.add(fp)
        rows.append(trigger)

    obs = getattr(slot, "input_observation_samples", None) or {}
    for sample in _pick_diverse_samples(obs, free_vars):
        row = _row_for_input(sample, free_vars)
        if row is None:
            continue
        fp = structural_input_fingerprint(row, free_variables=free_vars)
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        rows.append(row)
        if len(rows) >= _MAX_SEED_PATTERNS:
            break
    return rows


def _capture(new_source: str, slot_spec: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the new implementation over candidate rows; return aligned per-row records."""
    if not rows:
        return []
    gist = _build_contract_gist(
        implementation_source=new_source,
        free_variables=list(slot_spec.free_variables),
        sample_rows=rows,
        scaffold_source=slot_spec.enclosing_function_source,
        output_names=list(getattr(slot_spec, "output_names", None) or []),
    )
    recs = _run_batch_gist(gist)
    if len(recs) != len(rows):
        return []
    return recs


# ---------------------------------------------------------------------------
# Deterministic invariant seeding
# ---------------------------------------------------------------------------


def _expected_type_name(slot_spec: Any) -> str:
    et = slot_spec.expected_type
    name = getattr(et, "__name__", "")
    return name if name in _SAFE_TYPE_NAMES else ""


def _seed_invariants(
    *,
    slot_spec: Any,
    rows: list[dict[str, Any]],
    recs: list[dict[str, Any]],
    reason: str,
    effect: str,
    decision: str,
    commit_id: str,
) -> list[ContractCase]:
    """Derive carried-forward invariant cases from the new impl's actual behavior.

    Cases are derived from observed outputs, so they hold on the committing impl
    by construction (self-consistent contract)."""
    free_vars = list(slot_spec.free_variables)
    type_name = _expected_type_name(slot_spec)
    cases: list[ContractCase] = []
    for row, rec in zip(rows, recs):
        if rec.get("error") is not None:
            continue  # cannot characterise an input that raises
        fp = structural_input_fingerprint(row, free_variables=free_vars)
        primary = None
        for v in free_vars:
            if v != "self" and v in row:
                primary = row[v]
                break
        out_type = rec.get("type", "")

        def _mk(invariant: str, expected_type: str = "") -> ContractCase:
            cid = compute_case_id(
                kind="invariant",
                input_fingerprint=fp,
                invariant=invariant,
                expected_type=expected_type,
            )
            return ContractCase(
                case_id=cid,
                kind="invariant",
                input_sample=dict(row),
                input_fingerprint=fp,
                invariant=invariant,
                expected_type=expected_type,
                reason=reason,
                effect=effect,
                decision=decision,
                origin_commit_id=commit_id,
            )

        # non_empty: the new impl produced a non-empty result here.
        if not rec.get("is_empty"):
            cases.append(_mk("non_empty"))
        # non_identity: string output that transforms a sufficiently long string input.
        if (
            out_type == "str"
            and isinstance(primary, str)
            and len(primary.strip()) >= _IDENTITY_MIN_LEN
            and not rec.get("eq_primary")
        ):
            cases.append(_mk("non_identity"))
        # type_match: only for safe builtin return types that the impl actually returns.
        if type_name and out_type == type_name:
            cases.append(_mk("type_match", expected_type=type_name))
    return cases


# ---------------------------------------------------------------------------
# LLM pass
# ---------------------------------------------------------------------------


_MAINTAINER_SYSTEM = """\
You maintain a behavioral CONTRACT for a generated Python function: a small set \
of carried-forward checks that future regenerations must keep satisfying, so the \
system does not silently forget prior decisions.

You are given the spec (intent), the new implementation, concrete {input -> output} \
samples (indexed), the change record (what just changed and why), and the existing \
active cases.

Strongly PREFER data-agnostic checks over pinning exact outputs:
- Propose metamorphic relations ONLY from this fixed set: {relations}. A relation \
asserts the output is unchanged under a meaning-preserving input transform.
- Propose an "example" (pinned exact output) ONLY for a canonical, low-cardinality \
result the spec clearly intends (e.g. a fixed label/category). Never pin an output \
that is just one of many data-dependent values.
- Invariants are seeded automatically; only propose an invariant from {invariants} \
if a clearly-useful one is missing.

Supersede an existing EXAMPLE case ONLY when the change record shows its pinned \
output was deliberately changed by this commit (give the rationale in "why").

Reference inputs by their integer input_index. Keep proposals minimal and high-value: \
at most {max_examples} example cases. Each proposal must include a short reason and effect."""


def _create_model() -> tuple[Any, Any] | tuple[None, None]:
    import os

    config = get_config()
    api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None
    try:
        from pydantic_ai.models.openai import (
            OpenAIResponsesModel,
            OpenAIResponsesModelSettings,
        )

        return OpenAIResponsesModel(config.openai_model), OpenAIResponsesModelSettings()
    except Exception:
        return None, None


def _build_maintainer_prompt(
    *,
    spec_text: str,
    new_source: str,
    rows: list[dict[str, Any]],
    recs: list[dict[str, Any]],
    change_summary: str,
    active_cases: list[ContractCase],
) -> str:
    import json as _json

    parts = [f"## Spec (intent)\n{spec_text}"]
    parts.append(f"\n## New implementation\n```python\n{new_source}\n```")
    if change_summary:
        parts.append(f"\n## Change record\n{change_summary}")
    parts.append("\n## Candidate inputs and their outputs")
    for i, (row, rec) in enumerate(zip(rows, recs)):
        inp = _json.dumps(row, default=str)[:160]
        if rec.get("error") is not None:
            out = f"raised {rec.get('error')}"
        else:
            out = f"{rec.get('type')} {str(rec.get('repr'))[:160]}"
        parts.append(f"  [{i}] input={inp} -> {out}")
    if active_cases:
        parts.append("\n## Existing active cases")
        for c in active_cases[:25]:
            label = c.invariant or c.relation or "example"
            parts.append(f"  {c.case_id} kind={c.kind} check={label} reason={c.reason[:80]}")
    return "\n".join(parts)


async def _propose_async(prompt: str) -> ContractUpdate:
    from pydantic_ai import Agent

    model, settings = _create_model()
    if model is None:
        return ContractUpdate()
    cfg = get_config()
    # Targeted replacement (not str.format) so literal braces in the prose — e.g.
    # the "{input -> output}" sample notation — do not break templating.
    system = (
        _MAINTAINER_SYSTEM.replace("{relations}", ", ".join(relation_names()))
        .replace("{invariants}", ", ".join(INVARIANT_NAMES))
        .replace("{max_examples}", str(int(getattr(cfg, "contract_max_new_examples", 3))))
    )
    agent: Agent[None, ContractUpdate] = Agent(
        model, model_settings=settings, output_type=ContractUpdate, instructions=system
    )
    result = await agent.run(prompt)
    return result.output


def _apply_llm_proposals(
    *,
    update: ContractUpdate,
    contract: SlotContract,
    slot_spec: Any,
    new_source: str,
    rows: list[dict[str, Any]],
    recs: list[dict[str, Any]],
    decision: str,
    commit_id: str,
    max_examples: int,
) -> int:
    """Build, verify, and add proposed cases. Returns the count added."""
    free_vars = list(slot_spec.free_variables)
    candidates: list[ContractCase] = []
    examples_added = 0
    for p in update.new_cases:
        if p.input_index is None or not (0 <= p.input_index < len(rows)):
            continue
        row, rec = rows[p.input_index], recs[p.input_index]
        if rec.get("error") is not None:
            continue
        fp = structural_input_fingerprint(row, free_variables=free_vars)
        if p.kind == "example":
            if examples_added >= max_examples:
                continue
            cid = compute_case_id(
                kind="example",
                input_fingerprint=fp,
                expected_repr=str(rec.get("repr", "")),
                expected_type=str(rec.get("type", "")),
            )
            candidates.append(
                ContractCase(
                    case_id=cid, kind="example", input_sample=dict(row), input_fingerprint=fp,
                    expected_repr=str(rec.get("repr", "")), expected_type=str(rec.get("type", "")),
                    reason=p.reason, effect=p.effect, decision=decision, origin_commit_id=commit_id,
                )
            )
            examples_added += 1
        elif p.kind == "invariant" and p.invariant in INVARIANT_NAMES:
            cid = compute_case_id(kind="invariant", input_fingerprint=fp, invariant=p.invariant,
                                  expected_type=str(rec.get("type", "")))
            candidates.append(
                ContractCase(
                    case_id=cid, kind="invariant", input_sample=dict(row), input_fingerprint=fp,
                    invariant=p.invariant, expected_type=str(rec.get("type", "")),
                    reason=p.reason, effect=p.effect, decision=decision, origin_commit_id=commit_id,
                )
            )
        elif p.kind == "metamorphic" and p.relation in relation_names():
            cid = compute_case_id(kind="metamorphic", input_fingerprint=fp, relation=p.relation)
            candidates.append(
                ContractCase(
                    case_id=cid, kind="metamorphic", input_sample=dict(row), input_fingerprint=fp,
                    relation=p.relation, reason=p.reason, effect=p.effect,
                    decision=decision, origin_commit_id=commit_id,
                )
            )

    if not candidates:
        return 0
    # Verify proposals actually hold on the new source before adding (self-consistency).
    verify = run_contract(
        implementation_source=new_source, slot_spec=slot_spec,
        cases=candidates, scaffold_source=slot_spec.enclosing_function_source,
    )
    failed = verify.failing_case_ids()
    added = 0
    for case in candidates:
        if case.case_id in failed:
            continue
        contract.add(case)
        added += 1
    return added


def _apply_supersedes(update: ContractUpdate, contract: SlotContract) -> int:
    """Supersede existing active example cases the change deliberately broke."""
    count = 0
    for s in update.supersede:
        existing = contract.cases.get(s.case_id)
        if existing is None or existing.status != "active" or existing.kind != "example":
            continue
        contract.quarantine(s.case_id, f"superseded: {s.why}")
        count += 1
    return count


# ---------------------------------------------------------------------------
# Cap + entry point
# ---------------------------------------------------------------------------


def _enforce_cap(contract: SlotContract, cap: int) -> None:
    """Keep the active set bounded: quarantine the oldest active cases beyond ``cap``."""
    active = contract.active()
    if len(active) <= cap:
        return
    active.sort(key=lambda c: c.created_ts)  # oldest first
    for c in active[: len(active) - cap]:
        contract.quarantine(c.case_id, "evicted: active-case cap reached")


def maintain_contract(
    *,
    slot_spec: Any,
    runtime_values: dict[str, Any],
    new_source: str,
    change_record: dict | None,
    decision: str,
    commit_id: str,
    cache_dir: Any,
    session_id: str,
    portal_anchor: str,
    module_name: str,
    slot_id: str,
) -> None:
    """Update the slot's behavioral contract after a GENERATE/ADAPT (best-effort)."""
    cfg = get_config()
    if not getattr(cfg, "contract_enabled", True):
        return
    try:
        portal = load_portal(cache_dir, session_id, portal_anchor, module_name)
        slot = portal.slots.get(slot_id)
        if slot is None:
            return
        contract = get_contract(slot)

        rows = _candidate_rows(slot, slot_spec, runtime_values)
        recs = _capture(new_source, slot_spec, rows)
        if not recs:
            return

        cr = change_record_from_dict(change_record)
        reason = cr.reason or (
            "initial behavior" if decision == "GENERATE" else "adapted for new input pattern"
        )
        effect = cr.summary() if cr.effect_diff or cr.n_compared else "pins behavior for this input pattern"

        seeded = _seed_invariants(
            slot_spec=slot_spec, rows=rows, recs=recs,
            reason=reason, effect=effect, decision=decision, commit_id=commit_id,
        )
        for case in seeded:
            contract.add(case)
        n_added = len(seeded)
        n_superseded = 0

        if getattr(cfg, "contract_maintainer", False):
            try:
                from semipy.agents.decision import _run_async

                prompt = _build_maintainer_prompt(
                    spec_text=slot_spec.spec_text, new_source=new_source,
                    rows=rows, recs=recs, change_summary=cr.summary(),
                    active_cases=contract.active(),
                )
                update = _run_async(_propose_async(prompt))
                n_added += _apply_llm_proposals(
                    update=update, contract=contract, slot_spec=slot_spec,
                    new_source=new_source, rows=rows, recs=recs,
                    decision=decision, commit_id=commit_id,
                    max_examples=int(getattr(cfg, "contract_max_new_examples", 3)),
                )
                n_superseded = _apply_supersedes(update, contract)
            except Exception:
                pass

        _enforce_cap(contract, int(getattr(cfg, "contract_max_cases", 25)))
        save_contract(slot, contract)
        save_portal(cache_dir, portal)

        if cfg.verbose and (n_added or n_superseded):
            from semipy.agents.console_io import print_pipeline_log
            from semipy.types import SemiCallSite

            site = SemiCallSite(
                filename=slot_spec.source_span[0],
                lineno=slot_spec.source_span[1],
                func_qualname=slot_spec.enclosing_function_qualname,
            )
            if n_added:
                print_pipeline_log(
                    site, "contract", f"Recorded {n_added} behavioral case(s) for this slot."
                )
            if n_superseded:
                print_pipeline_log(
                    site, "contract",
                    f"Superseded {n_superseded} prior example(s) (deliberate change).",
                )
    except Exception as ex:
        if get_config().verbose:
            print(f"[semipy] Contract maintenance failed: {ex}", file=sys.stderr)
