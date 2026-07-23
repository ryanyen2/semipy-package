"""
LLM generation via pydantic_ai Agent + OpenAI Responses.

Agent has ONE tool: execute_action_program. The model writes a Python action program
that gathers evidence and tests a candidate function, then returns a CommitmentRecord
as structured output (generated_source + semantic metadata for #< skeleton lines).
"""
from __future__ import annotations

import inspect
import json
import textwrap
from typing import Any, Optional

from pydantic_ai import Agent, NativeOutput, RunContext

from semipy.agents.config import get_config
from semipy.agents.profiler import profile_runtime_context
from semipy.models import CommitmentRecord, SemiAgentDeps
from semipy.types import CacheEntry, SemiGenerationError


def _create_openai_model(config: Any) -> Any:
    """Create the coder's OpenAI Responses model + settings (reasoning enabled).

    Delegates to the centralized ``make_responses_model`` factory so the model id
    is resolved via ``config.model_for_role('coder')``. Generation requires a key,
    so a missing key raises here rather than degrading.
    """
    from semipy.orchestration.runtime import make_responses_model

    model, settings = make_responses_model("coder", reasoning=True)
    if model is None:
        raise ValueError(
            "OPENAI_API_KEY must be set (env or semi.configure(openai_api_key=...))"
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
        # Recurse into the type's own field/attribute annotations FIRST, so a
        # dependency (an Enum or nested dataclass a field references) is emitted
        # *before* the type that uses it -- otherwise the injected class source
        # raises NameError when exec'd (e.g. ``priority: Priority`` before Priority
        # is defined). This makes the injected block self-contained and ordered.
        try:
            import typing as _typing

            hints = _typing.get_type_hints(tp)
        except Exception:
            hints = getattr(tp, "__annotations__", {}) or {}
        for _h in list(hints.values()):
            _visit(_h)
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

    source: a STRING containing one complete function definition (def fn(...): ...);
            it is run via exec(), so pass the def as text, not as a live object.
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
        where result_dict is a JSON-serializable dict with the observation fields:
        data_profile, upstream_summary, gist_result, action_errors.

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


def _create_scoring_agent() -> Agent[SemiAgentDeps, CommitmentRecord]:
    """Decision-mode candidate agent: same tool + prompt as ``_create_agent``, but the
    model/settings come from ``make_scoring_model`` (logprob-instrumented) and the
    output type is ``NativeOutput`` so the finishing turn is a text message rather
    than a tool call -- pydantic_ai only attaches logprobs to text output.
    """
    from semipy.orchestration.runtime import make_scoring_model

    model, settings = make_scoring_model()
    if model is None:
        raise ValueError(
            "OPENAI_API_KEY must be set (env or semi.configure(openai_api_key=...))"
        )
    agent = Agent[SemiAgentDeps, CommitmentRecord](
        model,
        model_settings=settings,
        deps_type=SemiAgentDeps,
        output_type=NativeOutput(CommitmentRecord),
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
        where result_dict is a JSON-serializable dict with the observation fields:
        data_profile, upstream_summary, gist_result, action_errors.

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
        try:
            bundle = json.loads(result)
            gs = bundle.get("generated_source") or bundle.get("gist_result", {}) or {}
            if isinstance(gs, str) and gs.strip():
                deps.generated_source = gs
        except Exception:
            pass
        return result

    return agent


def _extract_mean_logprob(run_result: Any) -> Optional[float]:
    """Length-normalized mean log-prob of the finishing text message, or ``None``.

    ``NativeOutput`` mode makes the finishing turn a plain text message, which is
    the only shape pydantic_ai attaches ``provider_details['logprobs']`` to. Missing
    or malformed logprobs (wrong output shape, provider quirk) return ``None`` rather
    than raising -- callers treat that exactly like "no score for this candidate."
    """
    try:
        response = run_result.response
        parts = list(getattr(response, "parts", None) or [])
    except Exception:
        return None
    for part in reversed(parts):
        if type(part).__name__ != "TextPart":
            continue
        details = getattr(part, "provider_details", None) or {}
        logprobs = details.get("logprobs")
        if not logprobs:
            continue
        try:
            values = [float(lp["logprob"]) for lp in logprobs]
        except Exception:
            return None
        if not values:
            return None
        return sum(values) / len(values)
    return None


def generate_scored(spec: Any) -> tuple[CacheEntry, Optional[float]]:
    """Generate one decision-mode candidate, alongside its length-normalized mean
    log-prob (``None`` when logprobs aren't available -- callers fall back to naive
    vote-count weighting in that case).

    Single-attempt: unlike ``SemiAgent.generate``, this does not retry on validation
    failure. Decision-mode already draws several candidates per slot and drops any
    that fail (see ``slot_resolver._resolve_slot_with_decisions``), so a failed draw
    here is simply absorbed by the ensemble rather than retried in place.
    """
    from semipy.agents.compiler import _compile_source
    from semipy.agents.executor import GistExecutor
    from semipy.agents.validator import validate
    from semipy.orchestration.runtime import embed_run

    config = get_config()
    executor = GistExecutor(timeout=config.gist_timeout, e2b_api_key=config.e2b_api_key)
    deps = SemiAgentDeps(spec=spec, executor=executor)

    # SemiAgent._build_user_prompt depends only on `spec`; a throwaway instance
    # avoids duplicating that prompt logic here.
    from semipy.agents.agent import SemiAgent

    prompt = SemiAgent()._build_user_prompt(spec)
    agent = get_scoring_agent()

    async def _run() -> Any:
        try:
            return await agent.run(prompt, deps=deps)
        finally:
            if hasattr(executor, "close_async"):
                await executor.close_async()

    run_result = embed_run(_run())

    output = run_result.output
    if isinstance(output, CommitmentRecord):
        commitment_record: Optional[CommitmentRecord] = output
        source = (output.generated_source or "").strip()
    else:
        commitment_record = None
        source = (getattr(deps, "generated_source", None) or "").strip()

    if not source:
        raise SemiGenerationError(
            "Scoring agent did not produce a Python function. "
            "Check that the action program calls build_and_run_gist and the CommitmentRecord "
            "has generated_source set."
        )

    result = validate(
        source,
        expected_type=spec.expected_type,
        sample_input=spec.sample_input,
        enable_execution=True,
        spec=spec,
    )
    if not result.passed:
        raise SemiGenerationError(
            result.error_message or "Validation failed.",
            last_source=source,
            last_result=result,
        )

    fn = _compile_source(source)
    entry = CacheEntry(
        generated_source=source,
        compiled_fn=fn,
        expected_type=spec.expected_type,
        tool_calls_made=getattr(deps, "tool_calls_log", None),
        commitment_record=commitment_record,
    )
    score = _extract_mean_logprob(run_result)
    return entry, score


SYSTEM_PROMPT = """You generate a Python function that implements a user's semantic specification.

## Workflow

1. Write a Python action program and call execute_action_program(code).
   Available helpers (pre-defined in the sandbox):
     profile_slot()                     -> str   data profile + observed input values
     read_upstream()                    -> list  parent source when adapting (ADAPT only)
     build_and_run_gist(source, invoc)  -> dict  test a function; returns {success, result, error}

2. In the action program: assign your candidate to a STRING variable named `source`
   (a single complete `def ...`). Optionally call profile_slot() / read_upstream()
   first, then test it with build_and_run_gist(source, invocation_code) -- where
   invocation_code is a call expression that names YOUR function and passes a real
   test input. End the program with: print(_json.dumps({"gist_result": <result>}))

3. Return your answer as the CommitmentRecord structured output -- NOT a prose reply
   and NOT a fenced code block. Put the function text in generated_source:
   - generated_source: the exact function source that passed build_and_run_gist
   - goal: <= 12 words saying what the function produces (used for trace)
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

`source` is a STRING that build_and_run_gist runs via exec(); invocation_code calls
the function defined inside it BY NAME. The function name is your choice -- only the
name used in invocation_code has to match the `def`.

```python
ctx = profile_slot()  # inspect observed input values/shapes when useful

source = '''
def to_value(raw):
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    # real, data-agnostic logic driven by the spec goes here
    return text
'''

# invocation_code names the function above and passes a real test input.
gist = build_and_run_gist(source, "to_value('  example  ')")
print(_json.dumps({"gist_result": gist}))
```

## Notes

- `source` is a STRING holding one complete `def`. Do NOT define the candidate as
  live code in the action program and pass a bare `source` name -- that is a NameError.
- invocation_code is a call expression on that function name with test inputs. With an
  empty invocation the gist only defines the function (result is None) and tests nothing.
- If build_and_run_gist fails (or result is None when you expected a value), fix the
  `source` string or invocation_code and call execute_action_program again.
- STATEMENT_BLOCK slots (the user prompt says when one applies): the function returns a
  dict keyed by the output_names, e.g. {"result": value}, not a bare value.
- Do not hardcode observed sample values as constants -- implement for all possible inputs.
- Do not use keyword/substring match lists or fixed pattern tables; drive logic from the
  spec and the profiled data.
- Do not use emoji or docstrings.
"""


_semi_agent: Optional[Agent[SemiAgentDeps, CommitmentRecord]] = None
_scoring_agent: Optional[Agent[SemiAgentDeps, CommitmentRecord]] = None


def get_semi_agent() -> Agent[SemiAgentDeps, CommitmentRecord]:
    global _semi_agent
    if _semi_agent is None:
        _semi_agent = _create_agent()
    return _semi_agent


def get_scoring_agent() -> Agent[SemiAgentDeps, CommitmentRecord]:
    global _scoring_agent
    if _scoring_agent is None:
        _scoring_agent = _create_scoring_agent()
    return _scoring_agent
