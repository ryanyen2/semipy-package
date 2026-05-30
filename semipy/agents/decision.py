"""
Semantic reuse decision: gist-based evidence collection followed by one LLM
judgment call.

Flow:
1. Build a lightweight batch gist that runs the cached implementation against
   a representative sample of diverse observed inputs.
2. Execute the gist in a subprocess (no LLM, fast).
3. Collect the {input -> output} pairs.
4. Pass the spec, implementation, AND actual results to an LLM that decides
   ``reuse`` or ``adapt`` based on concrete evidence.

This ensures at most **one** LLM call per semantic evaluation (and zero if
the gist execution itself fails or no API key is configured).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Literal

from pydantic import BaseModel

from semipy.agents.config import get_config
from semipy.types import SlotSpec


def _run_async(coro: Any) -> Any:
    # Uses a fresh event loop per call rather than the persistent loop in agent.py.
    # Semantic checks run infrequently and in isolation, so a one-shot asyncio.run
    # is simpler and correct here — no streaming teardown races to avoid.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class SemanticDecision(BaseModel):
    """Structured output from the semantic decision agent."""

    decision: Literal["reuse", "adapt"]
    reasoning: str
    problematic_inputs: list[str] = []
    # Inputs that were resolved but admit multiple plausible interpretations.
    # Each entry is a dict with keys: input, picked_output, alternative_outputs, why.
    ambiguous_inputs: list[dict] = []
    # Which context signals moved the decision (for trace surfacing).
    context_changed: list[str] = []


# ---------------------------------------------------------------------------
# Step 1: build & run the batch gist
# ---------------------------------------------------------------------------

_GIST_SAMPLE_SIZE = 20


def _expr(value: Any) -> str:
    """Build a Python expression string safe for embedding in a gist."""
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_expr(x) for x in value)
        if isinstance(value, tuple) and len(value) == 1:
            return f"({inner},)"
        wrap = "()" if isinstance(value, tuple) else "[]"
        return f"{wrap[0]}{inner}{wrap[1]}"
    if isinstance(value, dict):
        parts = [f"{_expr(k)}: {_expr(v)}" for k, v in value.items()]
        return "{" + ", ".join(parts) + "}"
    return "None"


def _pick_diverse_samples(
    observations: dict[str, list[str]],
    free_variables: list[str],
    max_samples: int = _GIST_SAMPLE_SIZE,
) -> list[dict[str, str]]:
    """Select a representative sample of observation rows.

    Identifies the *primary* parameter (the one with the most diverse
    observations) and samples evenly from it.  For each sampled index,
    pulls aligned values from other observed parameters (cycling when
    the secondary list is shorter).  ``self`` is excluded.
    """
    all_obs: dict[str, list[str]] = {}
    for k, vals in observations.items():
        if k.startswith("_") or k == "self":
            continue
        if vals:
            all_obs[k] = vals

    if not all_obs:
        return []

    primary_key = None
    best_len = 0
    for fv in free_variables:
        if fv in all_obs and fv != "self" and len(all_obs[fv]) > best_len:
            primary_key = fv
            best_len = len(all_obs[fv])
    if primary_key is None:
        primary_key = max(all_obs, key=lambda k: len(all_obs[k]))

    primary_vals = all_obs[primary_key]
    step = max(1, len(primary_vals) // max_samples)
    sampled_indices = list(range(0, len(primary_vals), step))[:max_samples]

    rows: list[dict[str, str]] = []
    for idx in sampled_indices:
        row: dict[str, str] = {}
        for k, vals in all_obs.items():
            row[k] = vals[idx % len(vals)]
        rows.append(row)
    return rows


def _build_batch_gist(
    *,
    implementation_source: str,
    free_variables: list[str],
    sample_rows: list[dict[str, str]],
    scaffold_source: str | None = None,
) -> str:
    """Build a standalone Python script that runs the implementation against
    sampled inputs and prints JSON ``[{input: ..., output: ...}, ...]``.

    For each sample row the gist calls the implementation function with
    all ``free_variables``.  ``self`` is passed as ``None``.  When
    ``scaffold_source`` is provided, any variable derivations that precede
    the ``#>`` slot marker are included so derived parameters (like
    ``lower = text.lower()``) are computed from the primary input.
    """
    lines: list[str] = [
        "from __future__ import annotations",
        "import json",
        "import sys",
        "",
    ]

    lines.append(implementation_source)
    lines.append("")

    fn_name = _extract_fn_name(implementation_source)
    if not fn_name:
        return ""

    derivation_lines = _extract_scaffold_derivations(scaffold_source)

    non_self_vars = [v for v in free_variables if v != "self"]

    lines.append("_results = []")
    lines.append("_INPUTS = " + _expr(sample_rows))
    lines.append("for _row in _INPUTS:")

    for v in non_self_vars:
        lines.append(f"    {v} = _row.get({v!r}, '')")

    if derivation_lines:
        for dl in derivation_lines:
            lines.append(f"    {dl}")

    arg_parts: list[str] = []
    for v in free_variables:
        if v == "self":
            arg_parts.append("None")
        else:
            arg_parts.append(v)
    args_str = ", ".join(arg_parts)

    lines.append("    try:")
    lines.append(f"        _out = {fn_name}({args_str})")
    lines.append("    except Exception as _e:")
    lines.append("        _out = {'__error__': str(_e)}")

    if non_self_vars:
        primary = non_self_vars[0]
        input_expr = f"{primary}"
    else:
        input_expr = "str(_row)"
    lines.append(f"    _results.append({{'input': {input_expr}, 'output': _out}})")
    lines.append("")
    lines.append("print(json.dumps(_results, default=str))")
    return "\n".join(lines)


def _extract_scaffold_derivations(scaffold_source: str | None) -> list[str]:
    """Extract variable derivation lines from the scaffold that precede
    the first ``#>`` slot marker.

    For example, given::

        text = "" if body is None else str(body).strip()
        lower = text.lower()
        #> Classify this Apache error log body ...

    Returns ``['text = "" if body is None else str(body).strip()',
    'lower = text.lower()']``.
    """
    if not scaffold_source:
        return []
    result: list[str] = []
    in_function_body = False
    for raw_line in scaffold_source.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("def ") or stripped.startswith("@"):
            in_function_body = True
            continue
        if not in_function_body:
            continue
        if stripped.startswith("#>"):
            break
        if stripped and not stripped.startswith("#") and "=" in stripped:
            if not stripped.startswith("return") and not stripped.startswith("self."):
                result.append(stripped)
    return result


def _extract_fn_name(source: str) -> str:
    """Extract the first function name from generated source."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node.name
    return ""


def _run_batch_gist(gist_source: str, timeout: int = 15) -> list[dict[str, Any]]:
    """Execute the batch gist in a subprocess, return parsed results."""
    if not gist_source.strip():
        return []
    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(gist_source)
            path = f.name
        result = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return []
        stdout = result.stdout.strip()
        if not stdout:
            return []
        return json.loads(stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return []
    finally:
        if path:
            try:
                os.unlink(path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Step 2: LLM judgment on concrete evidence
# ---------------------------------------------------------------------------

_SEMANTIC_DECISION_SYSTEM = """\
You evaluate whether a generated Python function satisfies the **intent** \
of its spec for the inputs observed in a runtime session.

You receive:
1. The spec: the stated intent of the function (what it is supposed to do).
2. The surrounding scaffold (the formal code the spec sits in).
3. The generated implementation source code.
4. **Concrete test results**: actual {input -> output} pairs — either from \
running the implementation against sampled inputs or from real production \
calls recorded at runtime.
5. (Optional) A batch summary: total calls, raises, unique outputs.

Your primary question: **does each output satisfy the spec's intent for \
that input?**

Decision criteria:
- "reuse": outputs satisfy the spec's intent for the observed inputs. \
Minor cosmetic differences are acceptable.
- "adapt": there are CLEAR intent failures. Examples: inputs that should \
be parsed/classified/extracted are instead returning a generic fallback \
or default value (like the spec's "unless:" clause result); inputs from a \
new format that the current impl does not handle and silently falls through; \
outputs that are wrong for their inputs even if they pass type checks.

Ambiguity detection (populate ambiguous_inputs when found):
Flag inputs that the implementation resolved to ONE answer but where \
multiple legitimate interpretations exist under the spec (e.g. "01-02-2024" \
could be Jan 2 or Feb 1). List at most 3 such inputs with your best \
alternative. Ambiguous inputs alone do not force "adapt" — flag them but \
still return "reuse" unless they cause clear intent failures.

Be conservative: only say "adapt" when you see concrete evidence of intent \
failures. Do not flag cosmetic or style differences.

Keep "reasoning" under 3 sentences. List at most 5 problematic_inputs \
(the input values that got wrong outputs). List at most 3 ambiguous_inputs."""


def _create_decision_model() -> tuple[Any, Any] | tuple[None, None]:
    config = get_config()
    api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None
    try:
        from pydantic_ai.models.openai import (
            OpenAIResponsesModel,
            OpenAIResponsesModelSettings,
        )
        model = OpenAIResponsesModel(config.openai_model)
        settings = OpenAIResponsesModelSettings()
        return model, settings
    except Exception:
        return None, None


def _build_evidence_prompt(
    *,
    spec_text: str,
    scaffold_source: str | None,
    implementation_source: str,
    test_results: list[dict[str, Any]],
    slot_category: str,
    output_names: list[str],
    batch_summary: dict[str, Any] | None = None,
    real_data: bool = False,
) -> str:
    parts: list[str] = [f"## Spec (intent)\n{spec_text}"]
    if scaffold_source:
        parts.append(
            f"\n## Scaffold (surrounding formal code)\n```python\n{scaffold_source}\n```"
        )
    parts.append(
        f"\n## Generated implementation\n```python\n{implementation_source}\n```"
    )
    parts.append(f"\n## Slot category: {slot_category}")
    if output_names:
        parts.append(f"Output names: {output_names}")

    if batch_summary:
        n_in = batch_summary.get("n_in", 0)
        n_ret = batch_summary.get("n_returned", 0)
        n_raised = batch_summary.get("n_raised", 0)
        n_uniq = batch_summary.get("n_unique_outputs", 0)
        parts.append(
            f"\n## Batch summary (recent production calls)\n"
            f"  total_calls={n_in}, returned={n_ret}, raised={n_raised}, "
            f"unique_outputs={n_uniq}"
        )

    data_label = "Real production call results" if real_data else "Concrete test results"
    parts.append(f"\n## {data_label} ({len(test_results)} samples)")
    for i, row in enumerate(test_results, 1):
        inp = row.get("input", {})
        out = row.get("output", "")
        note = row.get("note", "")
        inp_str = json.dumps(inp, default=str) if isinstance(inp, dict) else str(inp)
        out_str = json.dumps(out, default=str) if isinstance(out, dict) else str(out)
        if len(inp_str) > 200:
            inp_str = inp_str[:197] + "..."
        if len(out_str) > 200:
            out_str = out_str[:197] + "..."
        note_suffix = f"  [{note}]" if note else ""
        parts.append(f"  {i}. input={inp_str}  -->  output={out_str}{note_suffix}")

    return "\n".join(parts)


async def _judge_async(
    *,
    spec_text: str,
    scaffold_source: str | None,
    implementation_source: str,
    test_results: list[dict[str, Any]],
    slot_category: str,
    output_names: list[str],
    batch_summary: dict[str, Any] | None = None,
    real_data: bool = False,
) -> SemanticDecision:
    from pydantic_ai import Agent

    model, settings = _create_decision_model()
    if model is None:
        return SemanticDecision(
            decision="reuse",
            reasoning="No API key available for semantic check; defaulting to reuse.",
        )

    agent: Agent[None, SemanticDecision] = Agent(
        model,
        model_settings=settings,
        output_type=SemanticDecision,
        instructions=_SEMANTIC_DECISION_SYSTEM,
    )

    prompt = _build_evidence_prompt(
        spec_text=spec_text,
        scaffold_source=scaffold_source,
        implementation_source=implementation_source,
        test_results=test_results,
        slot_category=slot_category,
        output_names=output_names,
        batch_summary=batch_summary,
        real_data=real_data,
    )

    result = await agent.run(prompt)
    return result.output


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_reuse_semantics(
    *,
    slot_spec: SlotSpec,
    implementation_source: str,
    session_observations: dict[str, list[str]] | None,
    call_outcomes: list[dict] | None = None,
    batch_summary: dict[str, Any] | None = None,
) -> SemanticDecision:
    """Evaluate whether the cached implementation satisfies the intent of its spec.

    When ``call_outcomes`` is provided (adaptive_mode), the real production
    input->output pairs are used directly as evidence, skipping the subprocess
    gist. When unavailable, falls back to the synthetic gist approach.

    Returns a SemanticDecision. On any failure defaults to ``reuse``.
    """
    real_data = False
    test_results: list[dict[str, Any]] = []

    if call_outcomes:
        test_results = call_outcomes[:_GIST_SAMPLE_SIZE]
        real_data = True

    if not test_results:
        if not session_observations:
            return SemanticDecision(
                decision="reuse", reasoning="No observations to evaluate."
            )

        sample_rows = _pick_diverse_samples(
            session_observations,
            list(slot_spec.free_variables),
        )
        if not sample_rows:
            return SemanticDecision(
                decision="reuse", reasoning="No diverse observations to evaluate."
            )

        gist_source = _build_batch_gist(
            implementation_source=implementation_source,
            free_variables=list(slot_spec.free_variables),
            sample_rows=sample_rows,
            scaffold_source=slot_spec.enclosing_function_source,
        )
        test_results = _run_batch_gist(gist_source)

        if not test_results:
            return SemanticDecision(
                decision="reuse",
                reasoning="Batch gist execution produced no results; defaulting to reuse.",
            )

    try:
        return _run_async(
            _judge_async(
                spec_text=slot_spec.spec_text,
                scaffold_source=slot_spec.enclosing_function_source,
                implementation_source=implementation_source,
                test_results=test_results,
                slot_category=slot_spec.expected_category.value,
                output_names=list(slot_spec.output_names or []),
                batch_summary=batch_summary,
                real_data=real_data,
            )
        )
    except Exception:
        return SemanticDecision(
            decision="reuse",
            reasoning="Semantic check encountered an error; defaulting to reuse.",
        )
