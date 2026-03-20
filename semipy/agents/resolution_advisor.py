"""
Optional LLM advisor: after a cross-slot reuse, decide if the borrowed implementation
is still appropriate or if this call site should regenerate (next invocation).
"""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from semipy.store import load_portal, save_portal

from semipy.agents.config import get_config
from semipy.agents.llm_utils import classify_with_llm


@dataclass
class ResolutionAdvisorVerdict:
    action: str
    commit_id: str = ""
    closest_commit_id: str = ""


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    for line in text.strip().splitlines():
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


def _parse_verdict(raw: str) -> ResolutionAdvisorVerdict:
    d = _extract_json_object(raw) or {}
    action = str(d.get("action", "")).strip().upper()
    if action not in ("REUSE", "GENERATE"):
        return ResolutionAdvisorVerdict(action="REUSE", commit_id=str(d.get("commit_id", "")))
    cid = str(d.get("commit_id", "")).strip()
    closest = str(d.get("closest_commit_id", "")).strip() or cid
    return ResolutionAdvisorVerdict(action=action, commit_id=cid, closest_commit_id=closest)


def build_resolution_advisor_prompt(
    *,
    current_spec: str,
    current_free_variables: list[str],
    current_call_site: str,
    expected_type_repr: str,
    candidate_summaries: list[str],
    chosen_commit_id: str,
    chosen_source_excerpt: str,
) -> str:
    examples = """
Example A (reuse): Same natural-language template and same placeholder arity; only the
surrounding dataframe column name differs. The implementation maps the placeholder to a
scalar and does not depend on upstream schema names.
Output: {"action":"REUSE","commit_id":"<chosen id>"}

Example B (regenerate): Same English prompt text but the return type or formal contract
changed (e.g. previously returned a string label, now must return a structured object),
or the implementation clearly assumes columns or fields that are not guaranteed at this
call site.
Output: {"action":"GENERATE","closest_commit_id":"<chosen id>"}

Example C (reuse): Standalone semi() moved to a new source line; template still
"{v0}s continent" with one free variable; semantics unchanged.
Output: {"action":"REUSE","commit_id":"<chosen id>"}

Example D (regenerate): Template literals match but enclosing formal code now passes a
different kind of value into the placeholder (e.g. region code vs country name), so the
old mapping would be systematically wrong.
Output: {"action":"GENERATE","closest_commit_id":"<chosen id>"}
"""
    cand_block = "\n".join(f"  - {c}" for c in candidate_summaries) if candidate_summaries else "  (none)"
    return f"""You choose whether a cached semiformal implementation may be shared with
a new call site, or whether the new call site must regenerate.

The only valid JSON shapes are:
{{"action":"REUSE","commit_id":"<20-char hex commit id from candidates>"}}
{{"action":"GENERATE","closest_commit_id":"<20-char hex id to use as adaptation parent>"}}

Rules:
- Output a single JSON object on one line. No markdown fences.
- If the semantic task, arity (number/order of template inputs), and type contract are
  the same, prefer REUSE with the commit id that was actually executed (chosen id below).
- If upstream data shape, type expectations, or meaning of the placeholders differ in a
  way that would make the existing code wrong, answer GENERATE and set closest_commit_id
  to the best parent (usually the chosen implementation below).

Illustrative cases (pattern only, not literal keywords to match):
{examples}

Current call site: {current_call_site}
Expected type (repr): {expected_type_repr}
Spec template (placeholders preserved): {current_spec!r}
Free variable names in order: {current_free_variables}

Candidates (newest first):
{cand_block}

Chosen implementation for this execution: commit_id={chosen_commit_id}
Excerpt of generated source:
```
{chosen_source_excerpt[:1800]}
```

Respond with one JSON object."""


async def advise_cross_slot_reuse_async(
    *,
    current_spec: str,
    current_free_variables: list[str],
    current_call_site: str,
    expected_type_repr: str,
    candidate_summaries: list[str],
    chosen_commit_id: str,
    chosen_source_excerpt: str,
    timeout: float = 20.0,
) -> ResolutionAdvisorVerdict:
    prompt = build_resolution_advisor_prompt(
        current_spec=current_spec,
        current_free_variables=current_free_variables,
        current_call_site=current_call_site,
        expected_type_repr=expected_type_repr,
        candidate_summaries=candidate_summaries,
        chosen_commit_id=chosen_commit_id,
        chosen_source_excerpt=chosen_source_excerpt,
    )
    return await classify_with_llm(
        prompt,
        parse_fn=_parse_verdict,
        default=ResolutionAdvisorVerdict(action="REUSE", commit_id=chosen_commit_id),
        timeout=timeout,
    )


def run_resolution_advisor_sync(**kwargs: Any) -> ResolutionAdvisorVerdict:
    return asyncio.run(advise_cross_slot_reuse_async(**kwargs))


def schedule_cross_slot_reuse_verification(
    *,
    cache_dir: Path,
    session_id: str,
    source_file: str,
    module_name: str,
    slot_spec: Any,
    donor_slot_id: str,
    donor_commit_id: str,
    candidate_summaries: list[str],
    chosen_source_excerpt: str,
) -> None:
    if not getattr(get_config(), "resolution_async_verify", False):
        return

    fn, ln, _ = slot_spec.source_span
    call_site = f"{fn}:{ln}"

    def work() -> None:
        try:
            verdict = run_resolution_advisor_sync(
                current_spec=slot_spec.spec_text,
                current_free_variables=list(slot_spec.free_variables),
                current_call_site=call_site,
                expected_type_repr=repr(slot_spec.expected_type),
                candidate_summaries=candidate_summaries,
                chosen_commit_id=donor_commit_id,
                chosen_source_excerpt=chosen_source_excerpt,
            )
            if verdict.action != "GENERATE":
                return
            portal = load_portal(cache_dir, session_id, source_file, module_name)
            s = portal.slots.get(slot_spec.slot_id)
            if s is None:
                return
            st = dict(getattr(s, "advisor_state", None) or {})
            st["force_regenerate_next"] = True
            closest = verdict.closest_commit_id or verdict.commit_id or donor_commit_id
            if closest:
                st["advisor_last_closest_commit"] = closest
            s.advisor_state = st
            save_portal(cache_dir, portal)
        except Exception:
            return

    threading.Thread(target=work, daemon=True).start()
