"""
LLM generation via pydantic_ai Agent + OpenAI Responses.

Agent has ONE tool: execute_action_program. The model writes a Python action program
that gathers evidence and tests a candidate function, then returns a CommitmentRecord
as structured output (generated_source + semantic metadata for #< skeleton lines).
"""
from __future__ import annotations

import inspect
import json
import os
import textwrap
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


def _collect_user_type_sources(expected_type: Any) -> list[tuple[str, str]]:
    """Return (class_name, dedented_source) for user-defined types found in expected_type.

    Traverses type args recursively. Skips builtins, typing internals, and stdlib modules.
    Returns [] when inspect.getsource fails (dynamically defined or builtin types).
    """
    seen: set[type] = set()
    results: list[tuple[str, str]] = []

    def _visit(tp: Any) -> None:
        if tp is None or tp is type(None):
            return
        origin = getattr(tp, "__origin__", None)
        if origin is not None:
            for arg in getattr(tp, "__args__", ()) or ():
                _visit(arg)
            return
        if not isinstance(tp, type):
            return
        module = getattr(tp, "__module__", "") or ""
        if module in ("builtins", "typing", "collections", "collections.abc") or module.startswith(
            ("typing", "_typing", "types")
        ):
            return
        if tp in seen:
            return
        seen.add(tp)
        try:
            src = inspect.getsource(tp)
            results.append((tp.__name__, textwrap.dedent(src)))
        except (OSError, TypeError):
            pass

    _visit(expected_type)
    return results


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

    # Collect user-defined type sources so the sandbox can instantiate them
    expected_type = getattr(spec, "expected_type", None)
    user_type_sources = _collect_user_type_sources(expected_type) if expected_type else []
    # Build preamble block: imports needed by dataclasses/enums + class definitions
    user_types_block = ""
    user_type_names: list[str] = []
    if user_type_sources:
        type_src_lines = ["# User-defined types available in this sandbox:"]
        needs_dataclass_import = False
        needs_enum_import = False
        for _cls_name, _cls_src in user_type_sources:
            if "@dataclass" in _cls_src:
                needs_dataclass_import = True
            if "Enum" in _cls_src or "enum" in _cls_src:
                needs_enum_import = True
            user_type_names.append(_cls_name)
        if needs_dataclass_import:
            type_src_lines.insert(0, "from dataclasses import dataclass, field")
        if needs_enum_import:
            type_src_lines.insert(0, "from enum import Enum")
        for _cls_name, _cls_src in user_type_sources:
            type_src_lines.append("")
            type_src_lines.extend(_cls_src.splitlines())
        user_types_block = "\n".join(type_src_lines) + "\n"

    user_type_names_repr = repr(user_type_names)

    profile_repr = repr(data_profile_summary)
    sources_repr = repr(parent_sources)
    obs_repr = repr(observations)

    return f"""import json as _json
import traceback as _tb

{user_types_block}
_SEMIPY_DATA_PROFILE = {profile_repr}
_SEMIPY_UPSTREAM = {sources_repr}
_SEMIPY_OBSERVATIONS = {obs_repr}
_SEMIPY_USER_TYPE_NAMES = {user_type_names_repr}


def profile_slot():
    \"\"\"Return pre-computed data profile (structure, value distributions, observations).\"\"\"
    return _SEMIPY_DATA_PROFILE


def read_upstream():
    \"\"\"Return list of parent implementation sources (strings) for adaptation.\"\"\"
    return _SEMIPY_UPSTREAM


class _SemiFx:
    \"\"\"Recording effect capability for testing effectful candidates in the sandbox.

    Mirrors semipy.effects.EffectRecorder's surface (create/read/update/delete/
    append/call) but records into a plain list so it needs no package import.
    \"\"\"
    def __init__(self):
        self.effects = []
    def _rec(self, op, target, payload=None, selector=None):
        self.effects.append((op, target, payload, selector))
        return (op, target)
    def create(self, target, payload=None):
        return self._rec("create", target, payload)
    def update(self, target, payload=None, selector=None):
        return self._rec("update", target, payload, selector)
    def delete(self, target, selector=None):
        return self._rec("delete", target, None, selector)
    def append(self, target, payload=None):
        return self._rec("append", target, payload)
    def read(self, target, selector=None):
        self._rec("read", target, None, selector)
        return []
    def call(self, target, payload=None):
        return self._rec("call", target, payload)
    @property
    def script(self):
        return self.effects


def build_and_run_gist(source, invocation_code=""):
    \"\"\"Execute a candidate function and return a result dict.

    source: complete function definition (def fn(...): ...)
    invocation_code: Python expression calling the function, e.g. 'fn_name("test input")'
    Returns dict with: success (bool), result (repr str or None), error (str or None)

    User-defined types from the slot's expected_type are pre-seeded into the execution
    namespace so the function can instantiate them without redefining them locally.
    \"\"\"
    # Seed exec namespace with user-defined types from this module's globals
    _ns = {{name: globals()[name] for name in _SEMIPY_USER_TYPE_NAMES if name in globals()}}
    # Effectful candidates declare an ``fx`` parameter; seed a recording fx so the
    # model can invoke and inspect them (e.g. invocation_code "fn(x, fx)").
    _ns["fx"] = _SemiFx()
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

1. Write a Python action program and call execute_action_program(code).
   Available helpers (pre-defined in the sandbox):
     profile_slot()                     -> str   data profile + observed input values
     read_upstream()                    -> list  parent source when adapting (ADAPT only)
     build_and_run_gist(source, invoc)  -> dict  test a function; returns {success, result, error}

2. In the action program: call profile_slot() when you need to inspect the data, call
   read_upstream() when adapting, define and test your candidate via build_and_run_gist,
   then end with: print(_json.dumps({"gist_result": <result>}))

3. Return a CommitmentRecord with:
   - generated_source: the exact function source that passed build_and_run_gist
   - goal: ≤ 12 words saying what the function produces (used for trace)
   - rejected_alternatives: brief notes on alternatives tried (optional)
   - annotations: leave this as an empty list. It is a deprecated field retained
     for backward compatibility; the steering surface is synthesised separately.

## Function requirements

- One function. Imports inside the body unless unavoidable.
- Match arity exactly: slot inputs are positional arguments in order.
- Handle edge cases: None inputs, empty strings, missing keys. Prefer safe defaults.
- No print statements, no docstrings, no sample invocations in the body.
- STATEMENT_BLOCK slots: return a dict with keys matching output_names exactly.
- list[SomeClass] return types: each element must be a class instance, not a plain dict.
- Hard constraints (formal_constraints lines) must appear verbatim in the implementation.
- ADAPT: call read_upstream() first. Preserve working logic; change only what failed.

## Action program example

```python
ctx = profile_slot()

def classify_body(self, body):
    text = "" if body is None else str(body).strip()
    lower = text.lower()
    if "scoreboard" in lower or "jk2_init" in lower:
        return {"family": "jk_child_scoreboard"}
    if "workerenv" in lower and "error" in lower:
        return {"family": "jk_worker_env_error"}
    return {"family": "unknown"}

gist = build_and_run_gist(
    source,
    "classify_body(None, 'jk2_init() Found child 1 in scoreboard slot 2')"
)
print(_json.dumps({"gist_result": gist}))
```

## Notes

- If build_and_run_gist fails, fix and call execute_action_program again.
- Do not hardcode observed sample values as constants — implement for all possible inputs.
- Do not use emoji or docstrings.
"""


_semi_agent: Optional[Agent[SemiAgentDeps, CommitmentRecord]] = None


def get_semi_agent() -> Agent[SemiAgentDeps, CommitmentRecord]:
    global _semi_agent
    if _semi_agent is None:
        _semi_agent = _create_agent()
    return _semi_agent
