"""
Post-validation steering synthesizer.

For each of the 6 structured-surface keys (``intent``, ``given``, ``by``,
``unless``, ``yields``, ``verified``) we compute a short deterministic
SHA over the minimal causal inputs. When the signature matches a prior commit's
entry, the value is carried forward verbatim (no LLM call). Only keys with a
changed signature (and no user-freeze) are re-synthesised via a single LLM call.

Several keys are handled deterministically so the LLM never hallucinates them:

* ``verified`` is derived from the validation result (sample input + gist
  stdout).  The LLM is never asked for it.
* ``yields`` is grounded in the AST of the generated source when the return
  statement is a dict literal or a trivial bare identifier.
* A minimum-set policy drops any key that would only restate the signature
  or the ``#>`` spec; empty-valued keys carry a valid input_sig so they do
  not look like missing data and keep stability.

Vocabulary (five primitives + verified):
  intent  — what the slot accomplishes (skip when spec is already one clear line)
  given   — input-shape assumption beyond the signature (multi-param only)
  by      — strategy/mechanism; embed "; because <reason>" inline when non-obvious
  unless  — fallback or exceptional path (skip when none in generated source)
  yields  — output shape (skip for simple builtins)
  verified— sample → output evidence (rule-derived, never LLM)

The resulting :class:`SteeringBlock` is attached to the :class:`CacheEntry` so
the writer can emit stable ``#< key: value`` lines around each slot anchor.
"""
from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import hashlib
import json
import os
from typing import Any

from pydantic import BaseModel, Field

from semipy.agents.config import get_config
from semipy.models import SteeringBlock, SteeringEntry


KEYS = ("intent", "given", "by", "unless", "yields", "verified")


def _h(obj: Any) -> str:
    """Short deterministic SHA256 hex for the given JSON-serialisable object."""
    try:
        raw = json.dumps(obj, sort_keys=True, default=str).encode()
    except Exception:
        raw = repr(obj).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _key_input_sig(key: str, spec: Any, entry: Any, slot: Any) -> str:
    """Compute a deterministic SHA256 hex over the causal inputs for this key."""
    slot_spec = getattr(spec, "slot_spec", None)
    advisor = getattr(slot, "advisor_state", None) or {}
    if not isinstance(advisor, dict):
        advisor = {}

    if key == "intent":
        slot_spec_local = getattr(spec, "slot_spec", None)
        spec_text = (getattr(slot_spec_local, "spec_text", "") or "") if slot_spec_local else ""
        return _h({"spec_text": spec_text})
    if key == "given":
        obs = dict(getattr(spec, "session_input_observations", None) or {})
        params: list[str] = []
        if slot_spec is not None:
            params = list(getattr(slot_spec, "free_variables", []) or [])
        upstream = list(getattr(spec, "upstream_lineage", None) or [])
        return _h({"obs": obs, "params": params, "upstream": upstream})
    if key == "yields":
        outs: list[str] = []
        if slot_spec is not None:
            outs = list(getattr(slot_spec, "output_names", []) or [])
        src = getattr(entry, "generated_source", "") or ""
        return _h(
            {
                "expected_type": repr(getattr(spec, "expected_type", "")),
                "output_names": outs,
                "return_shape": _extract_return_shape(src),
            }
        )
    if key == "by":
        src = getattr(entry, "generated_source", "") or ""
        return _h({"src_hash": hashlib.sha256(src.encode()).hexdigest()[:24]})
    if key == "unless":
        src = getattr(entry, "generated_source", "") or ""
        return _h({"src_hash": hashlib.sha256(src.encode()).hexdigest()[:24]})
    if key == "verified":
        validation = getattr(entry, "validation_result", None)
        passed = getattr(validation, "passed", "") if validation is not None else ""
        stdout = getattr(validation, "gist_stdout", "") if validation is not None else ""
        return _h(
            {
                "sample": repr(getattr(spec, "sample_input", None)),
                "passed": repr(passed),
                "stdout": stdout or "",
            }
        )
    return ""


# ---------------------------------------------------------------------------
# AST grounding helpers
# ---------------------------------------------------------------------------


def _extract_return_shape(generated_source: str) -> str:
    """Return a descriptive string for the function's return shape, or ``""``.

    Handles the common cases that matter for the ``yields`` key:
    * ``return {"foo": ..., "bar": ...}`` → ``"dict with keys {'foo', 'bar'}"``
    * ``return some_name`` → ``""`` (trivial — leave the type annotation speak)
    * Anything else → ``""``
    """
    if not generated_source or not generated_source.strip():
        return ""
    try:
        tree = ast.parse(generated_source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Return) or stmt.value is None:
                continue
            val = stmt.value
            if isinstance(val, ast.Dict):
                keys: list[str] = []
                for k in val.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.append(k.value)
                    else:
                        # Dynamic key — cannot describe precisely; fall back.
                        return "dict with dynamic keys"
                if keys:
                    shown = ", ".join(repr(k) for k in keys[:4])
                    suffix = ", ..." if len(keys) > 4 else ""
                    return f"dict with keys {{{shown}{suffix}}}"
                return "empty dict"
            if isinstance(val, ast.Name):
                return ""
        return ""
    return ""


# ---------------------------------------------------------------------------
# Rule-based `verified`
# ---------------------------------------------------------------------------


def _shortrepr(obj: Any, limit: int = 60) -> str:
    try:
        s = repr(obj)
    except Exception:
        s = str(obj)
    s = s.replace("\n", " ")
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _derive_verified(spec: Any, entry: Any, slot: Any) -> SteeringEntry:
    """Compute ``verified`` deterministically from the validation result.

    Format: ``<sample_repr> → <last_stdout_line>`` (no LLM call).
    """
    sig = _key_input_sig("verified", spec, entry, slot)
    val = getattr(entry, "validation_result", None)
    if val is None or not getattr(val, "passed", False):
        return SteeringEntry(value="", input_sig=sig)
    stdout = (getattr(val, "gist_stdout", "") or "").strip().splitlines()
    out_repr = stdout[-1][:80].strip() if stdout else ""
    sample = getattr(spec, "sample_input", None) or {}
    if isinstance(sample, dict):
        rv = sample.get("runtime_values") or sample.get("args") or sample
    else:
        rv = sample
    if isinstance(rv, dict) and not rv:
        rv = sample if isinstance(sample, dict) else {}
    sample_repr = _shortrepr(rv, 60) if rv else ""
    if out_repr and sample_repr:
        value = f"{sample_repr} → {out_repr}"
    else:
        value = out_repr or sample_repr
    value = _trim_words(value, 18)
    # Ground the effect surface in the behavioral contract when present.
    try:
        from semipy.contract.access import load_active_cases

        n_active = len(load_active_cases(slot))
        if n_active and value:
            value = f"{value} ({n_active} contract cases hold)"
    except Exception:
        pass
    return SteeringEntry(value=value, input_sig=sig)


# ---------------------------------------------------------------------------
# Minimum-set emptiness rules
# ---------------------------------------------------------------------------


_SIMPLE_BUILTINS: tuple[type, ...] = (str, int, float, bool, type(None))


def _has_exceptional_path(src: str) -> bool:
    """Return True when the generated source contains a raise, try/except, or ExceptHandler."""
    if not src:
        return False
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Raise, ast.ExceptHandler, ast.Try)):
                return True
    except SyntaxError:
        pass
    return False


def _should_skip_key(key: str, spec: Any, entry: Any) -> bool:
    """Return True when emitting ``key`` would only restate what is already visible.

    Each key has a concrete emptiness rule so the surface does not accumulate
    vacuous keys like ``given: date_str is scalar text`` when the signature is
    ``def infer_datetime_formatter(date_str: str) -> str``.
    """
    slot_spec = getattr(spec, "slot_spec", None)

    if key == "intent":
        spec_text = (getattr(slot_spec, "spec_text", "") or "") if slot_spec else ""
        # Skip when the spec is already one short clear line — the `#>` is the intent.
        compact = spec_text.strip()
        if not compact:
            return False
        one_line = "\n" not in compact
        return one_line and len(compact.split()) <= 12

    if key == "given":
        params = list(getattr(slot_spec, "free_variables", []) or []) if slot_spec else []
        # Single-param signatures have nothing to restate beyond the parameter name
        # and annotation — drop the key.
        if len(params) <= 1:
            return True
        return False

    if key == "yields":
        expected_type = getattr(spec, "expected_type", None)
        output_names = (
            list(getattr(slot_spec, "output_names", []) or []) if slot_spec else []
        )
        src = getattr(entry, "generated_source", "") or ""
        shape = _extract_return_shape(src)
        cat = ""
        if slot_spec is not None:
            cat_obj = getattr(slot_spec, "expected_category", None)
            cat = getattr(cat_obj, "value", "") or str(cat_obj or "")

        # STATEMENT_BLOCK with zero output_names: nothing to say.
        if cat == "statement" and not output_names:
            return True

        # STATEMENT_BLOCK with a single output that unwraps to a simple builtin:
        # the user sees a scalar at the `name = ...` binding site, so the dict
        # wrapping is an internal detail — do not surface `yields: {...}`.
        if (
            cat == "statement"
            and len(output_names) == 1
            and expected_type in _SIMPLE_BUILTINS
        ):
            return True

        # Simple builtin return annotation + no structural shape → skip.
        if expected_type in _SIMPLE_BUILTINS and not shape:
            return True
        return False

    if key == "by":
        # Always surface — this is the core strategy description.
        return False

    if key == "unless":
        src = getattr(entry, "generated_source", "") or ""
        return not _has_exceptional_path(src)

    if key == "verified":
        val = getattr(entry, "validation_result", None)
        return val is None or not getattr(val, "passed", False)

    return False


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


_FEW_SHOT = """\
### Example 1 — role-to-index mapping, fixed order
given: tokens=[strain_name, accession, collection_date, location] in fixed order
by: assigning fixed indices 0..3 to each role
unless: empty or non-iterable tokens, falls back to header split

### Example 2 — infer strptime pattern, str return
by: probing a regex-gated candidate table; because the table covers all observed formats without ambiguity
unless: empty or unmatched input, raises ValueError

### Example 3 — HTTP retry with backoff
intent: survive transient network failures
given: a request and a max attempt count
by: retrying on failure with exponentially growing delay; because bursts of retries make congestion worse
unless: attempts exhausted, yields the last error
yields: the successful response

### Example 4 — canonicalize host labels
by: mapping synonym clusters to one canonical token; unknown labels pass through unchanged
yields: dict[str, str] of label to short token

### Example 5 — JWT verification
intent: decide whether to trust a token
given: token, public key, clock
by: checking signature, then expiry, then issuer; because cheap checks reject obvious forgeries first
unless: any check fails, yields rejection with a reason
yields: the decoded claims
"""


def _truncate_source(source: str, max_lines: int = 25) -> str:
    lines = (source or "").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines]) + "\n# ... (truncated)"


def _build_synthesis_prompt(
    spec: Any,
    entry: Any,
    slot: Any,  # noqa: ARG001 (reserved for future use)
    changed_keys: list[str],
    prior: SteeringBlock | None,
    return_shape: str,
) -> str:
    """Assemble the synthesis prompt; asks the model to fill ONLY ``changed_keys``."""
    slot_spec = getattr(spec, "slot_spec", None)
    spec_text = (getattr(slot_spec, "spec_text", "") or "") if slot_spec else ""
    category = ""
    if slot_spec is not None:
        cat_obj = getattr(slot_spec, "expected_category", None)
        category = getattr(cat_obj, "value", "") or str(cat_obj or "")
    decision = str(getattr(spec, "decision", "") or "GENERATE")
    fail_ctx = getattr(spec, "verify_failure_context", "") or ""
    overrides = getattr(spec, "steering_overrides", None) or {}
    expected_type_repr = repr(getattr(spec, "expected_type", ""))

    generated = _truncate_source(getattr(entry, "generated_source", "") or "")

    unchanged_context: list[str] = []
    if prior is not None:
        for key in KEYS:
            if key in changed_keys:
                continue
            if key == "given":
                for idx, g in enumerate(prior.given):
                    if g.value:
                        unchanged_context.append(f"given[{idx}]: {g.value}")
            else:
                entry_obj = getattr(prior, key, None)
                value = getattr(entry_obj, "value", "") if entry_obj is not None else ""
                if value:
                    unchanged_context.append(f"{key}: {value}")

    overrides_block = ""
    if overrides:
        lines = [f"{k}: {v}" for k, v in overrides.items()]
        overrides_block = "\n### User-edited overrides (respect these)\n" + "\n".join(lines)

    unchanged_block = ""
    if unchanged_context:
        unchanged_block = "\n### Unchanged keys (for context only)\n" + "\n".join(unchanged_context)

    grounding_lines: list[str] = []
    if return_shape:
        grounding_lines.append(
            f"The generated function's return shape (AST-extracted): {return_shape}. "
            "Ground any `yields` value in this shape; do not restate the type annotation."
        )
    grounding_lines.append(
        f"Return type annotation: {expected_type_repr}."
    )

    parts = [
        "### Current slot",
        f"Spec: {spec_text}",
        f"Slot category: {category}",
        f"Decision: {decision}",
        "",
        "You fill a fixed keyword surface describing one semiformal slot implementation.",
        "Each value is a concrete phrase (<=16 words). Write phrases, not sentences.",
        "Keys: intent, given, by, unless, yields.",
        "- intent: what the slot accomplishes (one short phrase). Omit when obvious from the spec.",
        "- given: input-shape assumption beyond the signature. May repeat for 2-3 distinct facts.",
        "- by: the strategy/mechanism. When the choice is non-obvious, embed the reason inline: "
        "'<strategy>; because <reason>'. `because` is NOT a separate key — fold it into `by` or `unless`.",
        "- unless: the fallback or exceptional path, e.g. 'empty input raises ValueError' or "
        "'attempts exhausted, yields the last error'. May repeat for distinct failure modes. "
        "Return empty string (or omit from list) when there is no conditional/fallback path.",
        "- yields: output-shape commitment beyond the return annotation. Omit for side-effecting "
        "slots or when the return type annotation is already fully descriptive.",
        "- verified: NEVER synthesise — it is filled by rule from the validation result.",
        "",
        "MINIMUM-SET RULE: Return empty string for any key that only restates the signature, "
        "annotation, or `#>` spec. Use only the keys that earn their line. The primitives are a "
        "vocabulary, not a template — omit what adds no information.",
        "",
        "Return a JSON object with exactly these keys and phrase strings as values:",
        f"  {changed_keys}",
        "For 'given' and 'unless' you may return either a string or a list of up to 3 strings.",
        "",
        "### Few-shot examples",
        _FEW_SHOT,
    ]
    if grounding_lines:
        parts.append("")
        parts.append("Grounding:")
        parts.extend(f"- {g}" for g in grounding_lines)
    if fail_ctx:
        parts.append(f"Verify failure context: {fail_ctx}")
    change_summary = getattr(spec, "change_summary", "") or ""
    if change_summary:
        parts.append(
            f"Change reason/effect (ground `by`/`unless` in this): {change_summary}"
        )
    parts.append("")
    parts.append("### Generated source (first 25 lines)")
    parts.append("```python")
    parts.append(generated)
    parts.append("```")
    if overrides_block:
        parts.append(overrides_block)
    if unchanged_block:
        parts.append(unchanged_block)
    parts.append("")
    parts.append("Return only the JSON object, no commentary.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM structured output
# ---------------------------------------------------------------------------


class _SteeringSynthOutput(BaseModel):
    """Flexible structured container for fresh key values."""

    intent: str | None = Field(default=None)
    given: list[str] | str | None = Field(default=None)
    by: str | None = Field(default=None)
    unless: list[str] | str | None = Field(default=None)
    yields: str | None = Field(default=None)
    # `verified` omitted here — always rule-derived.


def _run_async(coro: Any) -> Any:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _synthesise_async(prompt: str) -> _SteeringSynthOutput | None:
    """One LLM call using the same OpenAI Responses pattern as ``decision.py``."""
    config = get_config()
    api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import (
            OpenAIResponsesModel,
            OpenAIResponsesModelSettings,
        )

        model = OpenAIResponsesModel(config.openai_model)
        settings = OpenAIResponsesModelSettings()
        agent: Agent[None, _SteeringSynthOutput] = Agent(
            model,
            model_settings=settings,
            output_type=_SteeringSynthOutput,
            instructions=(
                "Fill the requested keys with concrete phrase values (<=12 words). "
                "Return empty string for any key that would only restate the function "
                "signature or the spec. Return a JSON object matching the schema; "
                "only include the keys asked for."
            ),
        )
        result = await agent.run(prompt)
        return result.output
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Heuristic fallback (when the LLM call is unavailable)
# ---------------------------------------------------------------------------


def _heuristic_intent(spec: Any, entry: Any) -> str:
    record = getattr(entry, "commitment_record", None)
    goal = getattr(record, "goal", "") if record is not None else ""
    if goal:
        return _trim_words(goal.strip(), 12)
    slot_spec = getattr(spec, "slot_spec", None)
    spec_text = (getattr(slot_spec, "spec_text", "") or "") if slot_spec else ""
    return _trim_words(spec_text.strip().splitlines()[0] if spec_text else "", 12)


def _heuristic_yields(spec: Any, entry: Any) -> str:
    src = getattr(entry, "generated_source", "") or ""
    shape = _extract_return_shape(src)
    if shape:
        return _trim_words(shape, 14)
    exp = repr(getattr(spec, "expected_type", ""))
    slot_spec = getattr(spec, "slot_spec", None)
    outs: list[str] = []
    if slot_spec is not None:
        outs = list(getattr(slot_spec, "output_names", []) or [])
    if outs:
        return _trim_words(f"{exp} via {', '.join(outs)}", 12)
    return ""


def _trim_words(text: str, max_words: int) -> str:
    words = (text or "").split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _coerce_prior(prior: Any) -> SteeringBlock | None:
    if prior is None:
        return None
    if isinstance(prior, SteeringBlock):
        return prior
    try:
        return SteeringBlock.model_validate(prior)
    except Exception:
        return None


def _prior_entry(prior: SteeringBlock | None, key: str) -> SteeringEntry | list[SteeringEntry] | None:
    if prior is None:
        return None
    return getattr(prior, key, None)


def synthesize_steering(
    spec: Any,
    entry: Any,
    slot: Any,
    prior: Any,
    *,
    promoted_keys: dict[str, str] | None = None,
) -> SteeringBlock:
    """Compute a fresh :class:`SteeringBlock` for this commit.

    For each key, unchanged signatures carry values forward verbatim from
    ``prior`` (no LLM call). Only changed keys (with no user-freeze) are sent
    to the synthesis LLM. On LLM failure, a minimal heuristic SteeringBlock
    with at least ``goal`` populated is returned.

    ``verified`` is always rule-derived; ``yields`` is grounded in the AST of
    the generated source; the minimum-set policy skips keys that only restate
    the signature, return annotation, or ``#>`` spec.

    ``promoted_keys`` — keys whose value has been promoted to a ``#>`` line.
    Those entries are written with ``user_frozen=True`` and their value forced
    to empty so ``surface_skeleton`` never emits a ``#<`` line for them.
    """
    prior_block = _coerce_prior(prior)
    promoted = {k.lower() for k in (promoted_keys or {})}

    new_sigs: dict[str, str] = {}
    for key in KEYS:
        new_sigs[key] = _key_input_sig(key, spec, entry, slot)

    block = SteeringBlock()
    changed_keys: list[str] = []

    # Rule-based `verified` — never goes to the LLM.
    block.verified = _derive_verified(spec, entry, slot)

    for key in KEYS:
        if key == "verified":
            continue
        new_sig = new_sigs[key]
        prior_key = _prior_entry(prior_block, key)

        # Promoted keys are always frozen to empty so the writer omits them.
        if key in promoted:
            if key in ("given", "unless"):
                setattr(block, key, [])
            else:
                setattr(
                    block,
                    key,
                    SteeringEntry(value="", input_sig=new_sig, user_frozen=True),
                )
            continue

        if key in ("given", "unless"):
            prior_list = prior_key if isinstance(prior_key, list) else []
            all_user_frozen = bool(prior_list) and all(g.user_frozen for g in prior_list)
            all_same_sig = bool(prior_list) and all(g.input_sig == new_sig for g in prior_list)
            if all_user_frozen or all_same_sig:
                setattr(block, key, [
                    SteeringEntry(
                        value=g.value,
                        input_sig=g.input_sig or new_sig,
                        user_frozen=g.user_frozen,
                    )
                    for g in prior_list
                ])
                continue
            if _should_skip_key(key, spec, entry):
                setattr(block, key, [])
                continue
            changed_keys.append(key)
            continue

        assert prior_key is None or isinstance(prior_key, SteeringEntry)
        if prior_key is not None:
            if prior_key.user_frozen:
                setattr(
                    block,
                    key,
                    SteeringEntry(
                        value=prior_key.value,
                        input_sig=prior_key.input_sig or "",
                        user_frozen=True,
                    ),
                )
                continue
            if prior_key.input_sig and prior_key.input_sig == new_sig and prior_key.value:
                setattr(
                    block,
                    key,
                    SteeringEntry(
                        value=prior_key.value,
                        input_sig=prior_key.input_sig,
                        user_frozen=False,
                    ),
                )
                continue
        if _should_skip_key(key, spec, entry):
            setattr(
                block,
                key,
                SteeringEntry(value="", input_sig=new_sig, user_frozen=False),
            )
            continue
        changed_keys.append(key)

    if not changed_keys:
        return block

    return_shape = _extract_return_shape(getattr(entry, "generated_source", "") or "")
    prompt = _build_synthesis_prompt(
        spec, entry, slot, changed_keys, prior_block, return_shape
    )
    output: _SteeringSynthOutput | None = None
    try:
        output = _run_async(_synthesise_async(prompt))
    except Exception:
        output = None

    def _apply_simple(key: str, value: str | None) -> None:
        v = _trim_words((value or "").strip(), 14)
        sig = new_sigs[key]
        setattr(block, key, SteeringEntry(value=v, input_sig=sig, user_frozen=False))

    def _apply_list(key: str, max_items: int) -> None:
        raw: Any = getattr(output, key, None) if output is not None else None
        values: list[str] = []
        if isinstance(raw, list):
            values = [str(x).strip() for x in raw if str(x).strip()]
        elif isinstance(raw, str) and raw.strip():
            values = [raw.strip()]
        values = [_trim_words(v, 16) for v in values[:max_items]]
        setattr(block, key, [
            SteeringEntry(value=v, input_sig=new_sigs[key], user_frozen=False)
            for v in values
        ])

    for key in changed_keys:
        if key in ("given", "unless"):
            _apply_list(key, max_items=3 if key == "given" else 2)
            continue
        val = None
        if output is not None:
            val = getattr(output, key, None)
        _apply_simple(key, val if isinstance(val, str) else None)

    # Heuristic fallback: ensure `intent` is populated even when the LLM failed.
    if not block.intent.value and "intent" not in promoted:
        if not _should_skip_key("intent", spec, entry):
            _apply_simple("intent", _heuristic_intent(spec, entry))
    if not block.yields.value and "yields" not in promoted:
        if not _should_skip_key("yields", spec, entry):
            _apply_simple("yields", _heuristic_yields(spec, entry))

    return block
