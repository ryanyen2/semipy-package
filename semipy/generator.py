"""
LLM generation via pydantic_ai Agent + OpenAI.

Agent has tools: profile_data_and_flow, read_upstream_context, read_file_context,
build_and_run_gist, validate_output. Streaming and reasoning via run_stream_events.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from pydantic_ai import Agent, RunContext

from semipy.config import get_config
from semipy.models import (
    FileContextResult,
    GistRunResult,
    OutputValidationResult,
    ProfileDataResult,
    SemiAgentDeps,
    UpstreamContextResult,
)


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
        openai_reasoning_effort='low',
        openai_reasoning_summary='auto',
    )
    return model, settings


def _create_openrouter_model(config: Any) -> Any:
    """Create OpenRouter model and settings for pydantic_ai."""
    from pydantic_ai.models.openrouter import (
        OpenRouterModel,
        OpenRouterModelSettings,
        OpenRouterProvider,
    )
    api_key = config.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY must be set (env or semi.configure(openrouter_api_key=...))"
        )
    model = OpenRouterModel(
        config.openrouter_model,
        provider=OpenRouterProvider(api_key=api_key),
    )
    settings = OpenRouterModelSettings(
        openrouter_reasoning={"effort": "high"},
        temperature=0.0,
    )
    return model, settings


def _create_agent() -> Agent[SemiAgentDeps]:
    """Lazy creation of pydantic_ai Agent with OpenAI and tools."""
    config = get_config()
    model, settings = _create_openai_model(config)
    agent = Agent[SemiAgentDeps, str](
        model,
        model_settings=settings,
        deps_type=SemiAgentDeps,
        system_prompt=SYSTEM_PROMPT,
    )

    @agent.tool
    async def profile_data_and_flow(
        ctx: RunContext[SemiAgentDeps],
        code: str,
        working_dir: Optional[str] = None,
    ) -> ProfileDataResult:
        """Run FIRST when the user provides data analysis code. Returns data_profile, data_flow, summary. Set working_dir if code uses relative paths."""
        try:
            from semipy.agents.refs.example_glm import profile_data_and_flow_impl
        except ImportError:
            return ProfileDataResult(
                success=False,
                error="profile_data_and_flow_impl not available (refs not installed)",
            )
        raw = profile_data_and_flow_impl(code, timeout=15, working_dir=working_dir)
        from semipy.models import DataFlowStep
        flow = [DataFlowStep(**s) for s in raw.get("data_flow", [])]
        return ProfileDataResult(
            success=raw.get("success", False),
            error=raw.get("error"),
            data_profile=raw.get("data_profile", {}),
            data_flow=flow,
            summary=raw.get("summary", ""),
            insights_placeholder=raw.get("insights_placeholder"),
        )

    @agent.tool
    async def read_upstream_context(ctx: RunContext[SemiAgentDeps]) -> UpstreamContextResult:
        """Read parent implementation sources when adapting from a previous commit."""
        deps = ctx.deps
        spec = getattr(deps, "spec", None)
        if not spec or not getattr(spec, "parent_sources", None):
            return UpstreamContextResult(success=True, sources=[], summary="No parent sources.")
        return UpstreamContextResult(
            success=True,
            sources=list(spec.parent_sources),
            summary=f"{len(spec.parent_sources)} parent implementation(s) available.",
        )

    @agent.tool
    async def read_file_context(
        ctx: RunContext[SemiAgentDeps],
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> FileContextResult:
        """Read source file content, optionally a line range."""
        try:
            from pathlib import Path
            p = Path(file_path).resolve()
            if not p.exists():
                return FileContextResult(success=False, error=f"File not found: {file_path}")
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            if start_line is not None and end_line is not None:
                start = max(0, start_line - 1)
                end = min(len(lines), end_line)
                content = "\n".join(lines[start:end])
            else:
                content = "\n".join(lines)
            return FileContextResult(success=True, content=content)
        except Exception as e:
            return FileContextResult(success=False, error=str(e))

    @agent.tool
    async def build_and_run_gist(ctx: RunContext[SemiAgentDeps], generated_function_source: str) -> GistRunResult:
        """Assemble a minimal runnable gist (user context + generated function), execute in sandbox, return stdout/stderr/result. Call this to test your generated function."""
        deps = ctx.deps
        gist_builder = getattr(deps, "gist_builder", None)
        executor = getattr(deps, "executor", None)
        if not gist_builder or not executor:
            return GistRunResult(success=False, error="GistBuilder or Executor not in deps")
        gist = gist_builder.build(generated_function_source)
        if get_config().verbose:
            print("[Gist built] (sandbox test: generated function + sample invocation)")
            print("---")
            print(gist.source)
            print("---")
        if not gist:
            return GistRunResult(
                success=False,
                error="Could not build gist (missing user source or AST trace failed).",
            )
        result = await executor.execute_async(gist.source)
        deps.generated_source = generated_function_source
        deps.tool_calls_log.append("build_and_run_gist")
        return GistRunResult(
            success=result.success,
            stdout=result.stdout,
            stderr=result.stderr,
            result_repr=result.result_repr,
            error=result.error,
        )

    @agent.tool
    async def validate_output(
        ctx: RunContext[SemiAgentDeps],
        result_repr: str,
        expected_type_name: str,
    ) -> OutputValidationResult:
        """Check that result_repr (from gist exec) parses to the expected type. Uses Python literal parsing so the exec result is validated correctly."""
        import ast as _ast
        s = result_repr.strip()
        try:
            if expected_type_name in ("bool", "boolean"):
                try:
                    val = _ast.literal_eval(s)
                except (ValueError, SyntaxError):
                    lower = s.lower()
                    if lower in ("true", "1", "yes"):
                        val = True
                    elif lower in ("false", "0", "no", ""):
                        val = False
                    else:
                        return OutputValidationResult(
                            valid=False,
                            message=f"Cannot parse as bool: {s!r}",
                            expected_type=expected_type_name,
                        )
                if not isinstance(val, bool):
                    return OutputValidationResult(
                        valid=False,
                        message=f"Parsed value is {type(val).__name__}, not bool",
                        expected_type=expected_type_name,
                        actual_type=type(val).__name__,
                    )
            elif expected_type_name == "str":
                try:
                    val = _ast.literal_eval(s)
                except (ValueError, SyntaxError):
                    val = s.strip("'\"")
                if not isinstance(val, str):
                    return OutputValidationResult(
                        valid=False,
                        message=f"Parsed value is {type(val).__name__}, not str",
                        expected_type=expected_type_name,
                        actual_type=type(val).__name__,
                    )
            elif expected_type_name == "int":
                val = _ast.literal_eval(s) if s else 0
                if not isinstance(val, int):
                    return OutputValidationResult(
                        valid=False,
                        message=f"Parsed value is {type(val).__name__}, not int",
                        expected_type=expected_type_name,
                        actual_type=type(val).__name__,
                    )
            elif expected_type_name == "float":
                val = _ast.literal_eval(s) if s else 0.0
                if not isinstance(val, (int, float)):
                    return OutputValidationResult(
                        valid=False,
                        message=f"Parsed value is {type(val).__name__}, not float",
                        expected_type=expected_type_name,
                        actual_type=type(val).__name__,
                    )
            elif expected_type_name == "list":
                val = _ast.literal_eval(s) if s else []
                if not isinstance(val, list):
                    return OutputValidationResult(
                        valid=False,
                        message=f"Parsed value is {type(val).__name__}, not list",
                        expected_type=expected_type_name,
                        actual_type=type(val).__name__,
                    )
            else:
                return OutputValidationResult(
                    valid=True,
                    message=f"Cannot validate type {expected_type_name}; assuming valid.",
                    expected_type=expected_type_name,
                )
            return OutputValidationResult(
                valid=True,
                message="Type check passed.",
                expected_type=expected_type_name,
                actual_type=type(val).__name__,
            )
        except Exception as e:
            return OutputValidationResult(
                valid=False,
                message=str(e),
                expected_type=expected_type_name,
            )

    return agent


SYSTEM_PROMPT = """You generate a single Python function that implements the user's semantic request.

Rules:
- Output only one function. No explanations, no markdown outside the code block.
- Wrap the function in a ```python code block.
- The function must be pure Python unless the request clearly suggests external interaction (e.g. fetching data). Use standard library or requests; do not rely on built-in domain-specific tools unless the prompt explicitly asks for them.
- Parameters: the user prompt may reference "the value" or "this row"; those become the first parameter(s). Other fixed context are described in the prompt; bake them into the function or add parameters as needed.
- Return type: match exactly what the user needs (bool for conditions, str for text, int/float for numbers, or the described type). Prefer a typed signature when the return type is known (e.g. def f(row, c3) -> bool:). The pipeline preserves type annotations.
- Handle edge cases: None, missing keys, empty data. Prefer safe defaults over raising.
- Use the data context (variable_values, sample_input) when provided; use actual column names and values from the context. Never fabricate data values.
- When a usage context is provided (e.g. "passed as argument to X"), return the type that X expects.
- Do not use emoji or decorative output. No docstrings or comments in the code, just code.
- When the user provides a previous implementation (adapt or inspiration), preserve its structure where possible and change only what is needed.

Tool usage:
1. If the spec contains data variables or sample data, you may call profile_data_and_flow(code, working_dir?) first to get data profile and flow.
2. If adapting from a parent, call read_upstream_context() to read parent sources.
3. Generate the function, then call build_and_run_gist(generated_function_source) with the raw function source only: the exact "def name(...):" and body as a string. No markdown, no ```python wrapper, no surrounding text.
4. If the gist fails (success=False), read stderr and fix the function; call build_and_run_gist again with the corrected source.
5. When the gist succeeds, call validate_output(result_repr, expected_type_name) with the result_repr from the gist tool result (and expected_type_name from the user request, e.g. "bool", "list", "str").
6. Output the final function in a ```python code block."""


_semi_agent: Optional[Agent[SemiAgentDeps]] = None


def get_semi_agent() -> Agent[SemiAgentDeps]:
    global _semi_agent
    if _semi_agent is None:
        _semi_agent = _create_agent()
    return _semi_agent
