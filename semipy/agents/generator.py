"""
LLM generation via pydantic_ai Agent + OpenAI Responses.

Agent has ONE tool: execute_action_program. The model writes a Python action program
that gathers evidence and tests a candidate function, then returns a CommitmentRecord
as structured output (generated_source + semantic metadata for #< skeleton lines).
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from pydantic_ai import Agent, RunContext

from semipy.agents.config import get_config
from semipy.agents.profiler import profile_runtime_context
from semipy.models import CommitmentRecord, SemiAgentDeps


def _create_openai_model(config: Any) -> Any:
    """Create OpenAI model and settings for pydantic_ai."""
    from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
    api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY must be set (env or semi.configure(openai_api_key=...))"
        )
    model = OpenAIResponsesModel(config.openai_model)
    settings = OpenAIResponsesModelSettings(
        openai_reasoning_effort='medium',
        openai_reasoning_summary='auto',
        openai_send_reasoning_ids=True,
    )
    return model, settings


def _build_action_preamble(spec: Any) -> str:
    """Build the Python preamble for the action program with embedded runtime context.

    The preamble defines three helper functions available to the model's action program:
      profile_slot()  -> str   pre-computed data profile summary
      read_upstream() -> list  parent implementation sources (for ADAPT)
      build_and_run_gist(source, invocation_code) -> dict  test a candidate function
    """
    # Compute data profile from spec
    data_profile_summary = ""
    sample_input = getattr(spec, "sample_input", None)
    if isinstance(sample_input, dict):
        runtime_values = sample_input.get("runtime_values", None)
        if isinstance(runtime_values, dict) and runtime_values:
            data_profile_summary = profile_runtime_context(
                locals_dict={},
                variable_values=runtime_values,
                total_budget=10000,
                collection_budget=6000,
            )
    obs = getattr(spec, "session_input_observations", None)
    if isinstance(obs, dict) and obs:
        extra = ["\nSession observations (distinct values per parameter):"]
        for k in sorted(obs.keys()):
            v = obs[k]
            if isinstance(v, list):
                extra.append(f"  {k}: {v[:10]}")
        data_profile_summary += "\n" + "\n".join(extra)

    parent_sources: list[str] = list(getattr(spec, "parent_sources", None) or [])
    observations: dict[str, list[str]] = {}
    if isinstance(obs, dict):
        observations = {k: v[:20] if isinstance(v, list) else v for k, v in obs.items()}

    profile_repr = repr(data_profile_summary)
    sources_repr = repr(parent_sources)
    obs_repr = repr(observations)

    return f"""import json as _json
import traceback as _tb

_SEMIPY_DATA_PROFILE = {profile_repr}
_SEMIPY_UPSTREAM = {sources_repr}
_SEMIPY_OBSERVATIONS = {obs_repr}


def profile_slot():
    \"\"\"Return pre-computed data profile (structure, value distributions, observations).\"\"\"
    return _SEMIPY_DATA_PROFILE


def read_upstream():
    \"\"\"Return list of parent implementation sources (strings) for adaptation.\"\"\"
    return _SEMIPY_UPSTREAM


def build_and_run_gist(source, invocation_code=""):
    \"\"\"Execute a candidate function and return a result dict.

    source: complete function definition (def fn(...): ...)
    invocation_code: Python expression calling the function, e.g. 'fn_name("test input")'
    Returns dict with: success (bool), result (repr str or None), error (str or None)
    \"\"\"
    _ns = {{}}
    try:
        exec(compile(source, "<gist>", "exec"), _ns)
    except Exception:
        return {{"success": False, "result": None, "error": _tb.format_exc()}}
    if not invocation_code:
        return {{"success": True, "result": None, "error": None}}
    try:
        _result = eval(invocation_code, _ns)
        return {{"success": True, "result": repr(_result), "error": None}}
    except Exception:
        return {{"success": False, "result": None, "error": _tb.format_exc()}}


# === Action program (model-generated code below) ===
"""


def _create_agent() -> Agent[SemiAgentDeps, CommitmentRecord]:
    """Lazy creation of pydantic_ai Agent with OpenAI Responses and the single action program tool."""
    config = get_config()
    model, settings = _create_openai_model(config)
    agent = Agent[SemiAgentDeps, CommitmentRecord](
        model,
        model_settings=settings,
        deps_type=SemiAgentDeps,
        output_type=CommitmentRecord,
        system_prompt=SYSTEM_PROMPT,
    )

    @agent.tool
    async def execute_action_program(ctx: RunContext[SemiAgentDeps], code: str) -> str:
        """Execute a Python action program in E2B to gather evidence and test your candidate function.

        The program has access to three helper functions:
          profile_slot()                        -> str   data profile summary
          read_upstream()                       -> list  parent implementation sources
          build_and_run_gist(source, invoc)     -> dict  test a function (success, result, error)

        The program MUST end with: print(_json.dumps(result_dict))
        where result_dict is a JSON-serializable dict (matching ObservationBundle fields:
        data_profile, upstream_summary, gist_result, action_errors).

        Returns the JSON string from the program's stdout.
        """
        deps = ctx.deps
        executor = getattr(deps, "executor", None)
        if executor is None:
            return json.dumps({"action_errors": ["No executor in deps"]})
        spec = getattr(deps, "spec", None)
        preamble = _build_action_preamble(spec) if spec else "import json as _json\n\n"
        composed = preamble + "\n" + code
        result = await executor.execute_action_program_async(composed)
        # Store the generated_source from the bundle if present
        try:
            bundle = json.loads(result)
            gs = bundle.get("generated_source") or bundle.get("gist_result", {}) or {}
            if isinstance(gs, str) and gs.strip():
                deps.generated_source = gs
        except Exception:
            pass
        return result

    return agent


SYSTEM_PROMPT = """You generate a Python function that implements a user's semantic specification.

## Workflow

1. Write a Python action program and call execute_action_program(code) with it.
   The program runs in a sandboxed Python environment (E2B) with these helpers pre-defined:
     - profile_slot()                     -> str   pre-computed data profile and observations
     - read_upstream()                    -> list  parent implementation sources (ADAPT only)
     - build_and_run_gist(source, invoc)  -> dict  test your function; returns {success, result, error}

2. In the action program:
   - Optionally call profile_slot() to inspect the data context.
   - Optionally call read_upstream() when adapting from a parent implementation.
   - Define your candidate function.
   - Call build_and_run_gist with the function source and a representative invocation.
   - End with: print(_json.dumps(result_dict))
     where result_dict includes at minimum {"gist_result": <build_and_run_gist result>}.

3. After receiving the tool result (JSON from the action program), return a CommitmentRecord:
   - generated_source: the exact function source that passed build_and_run_gist
   - goal: ≤ 20 words describing what the slot produces
   - givens: list of key observed evidence (data shape, types, value patterns) — max 5
   - assumptions: list of accepted assumptions (e.g., "text is non-null UTF-8") — max 5
   - decision_points: list of key implementation choices (e.g., "used regex not strptime") — max 5
   - checks_performed: list of validation steps done (e.g., "gist passed with sample input") — max 5
   - downstream_expectations: list of what callers require from the output — max 3
   - rejected_alternatives: list of alternatives tried and rejected — max 3, optional

## Function requirements

- Output one function. No imports outside the function body unless necessary.
- Slot inputs are listed in the user prompt as positional arguments in exact order. Match arity.
- Handle edge cases: None inputs, empty strings, missing keys. Prefer safe defaults over raising.
- No print statements. No sample invocations inside the function body. Return only the value.
- For STATEMENT_BLOCK slots: return a dict with keys matching output_names exactly.
- For list[SomeClass] return types: each element must be a class instance, not a plain dict.
- Hard constraints (formal_constraints lines) must appear verbatim in the implementation.
- When adapting (ADAPT), call read_upstream() first to read the parent implementation.
  Preserve working logic and change only what the failure reason requires.

## Action program example

```python
# Inspect data context
ctx = profile_slot()

# Define candidate
def classify_log_level(line):
    if "ERROR" in line.upper():
        return "error"
    if "WARN" in line.upper():
        return "warning"
    return "info"

# Test it
gist = build_and_run_gist(
    \"\"\"def classify_log_level(line):
    if 'ERROR' in line.upper():
        return 'error'
    if 'WARN' in line.upper():
        return 'warning'
    return 'info'\"\"\",
    "classify_log_level('2024-01-15 ERROR disk full')"
)

print(_json.dumps({"gist_result": gist}))
```

## Notes

- If build_and_run_gist fails (success=False), fix the function and call execute_action_program again with a corrected program.
- Do not embed sample data values as hardcoded constants — implement for all values in the column/collection.
- Do not use emoji or docstrings. Code only.
"""


_semi_agent: Optional[Agent[SemiAgentDeps, CommitmentRecord]] = None


def get_semi_agent() -> Agent[SemiAgentDeps, CommitmentRecord]:
    global _semi_agent
    if _semi_agent is None:
        _semi_agent = _create_agent()
    return _semi_agent
