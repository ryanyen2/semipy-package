"""
LLM generation via pydantic_ai Agent + OpenRouter.

Replaces DSPy with an agent that has tools: profile_data_and_flow,
read_upstream_context, read_file_context, build_and_run_gist, validate_output.
Streaming and reasoning are visible via run_stream_events.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from semipy.config import get_config
from semipy.models import (
    FileContextResult,
    GistRunResult,
    OutputValidationResult,
    ProfileDataResult,
    SemiAgentDeps,
    UpstreamContextResult,
)


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


def _create_agent() -> Any:
    """Lazy creation of pydantic_ai Agent with OpenRouter and tools."""
    from pydantic_ai import Agent

    config = get_config()
    model, settings = _create_openrouter_model(config)
    agent = Agent(
        model,
        model_settings=settings,
        deps_type=SemiAgentDeps,
        system_prompt=SYSTEM_PROMPT,
    )

    @agent.tool
    async def profile_data_and_flow(
        ctx: Any,
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
    async def read_upstream_context(ctx: Any) -> UpstreamContextResult:
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
        ctx: Any,
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
    async def build_and_run_gist(ctx: Any, generated_function_source: str) -> GistRunResult:
        """Assemble a minimal runnable gist (user context + generated function), execute in sandbox, return stdout/stderr/result. Call this to test your generated function."""
        deps = ctx.deps
        gist_builder = getattr(deps, "gist_builder", None)
        executor = getattr(deps, "executor", None)
        if not gist_builder or not executor:
            return GistRunResult(success=False, error="GistBuilder or Executor not in deps")
        gist = gist_builder.build(generated_function_source)
        if not gist:
            return GistRunResult(
                success=False,
                error="Could not build gist (missing user source or AST trace failed).",
            )
        result = executor.execute_sync(gist.source)
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
        ctx: Any,
        result_repr: str,
        expected_type_name: str,
    ) -> OutputValidationResult:
        """Check that a result string parses to the expected type (e.g. bool, str, int)."""
        try:
            if expected_type_name in ("bool", "boolean"):
                val = result_repr.strip().lower() in ("true", "1", "yes")
            elif expected_type_name == "str":
                val = result_repr.strip().strip("'\"")
            elif expected_type_name == "int":
                val = int(result_repr.strip())
            elif expected_type_name == "float":
                val = float(result_repr.strip())
            elif expected_type_name == "list":
                val = eval(result_repr) if result_repr.strip() else []
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
- Return type: match exactly what the user needs (bool for conditions, str for text, int/float for numbers, or the described type).
- Handle edge cases: None, missing keys, empty data. Prefer safe defaults over raising.
- Use the data context (variable_values, sample_input) when provided; use actual column names and values from the context. Never fabricate data values.
- When a usage context is provided (e.g. "passed as argument to X"), return the type that X expects.
- Do not use emoji or decorative output. No docstrings or comments in the code, just code.
- When the user provides a previous implementation (adapt or inspiration), preserve its structure where possible and change only what is needed.

Tool usage:
1. If the spec contains data variables or sample data, you may call profile_data_and_flow(code, working_dir?) first to get data profile and flow.
2. If adapting from a parent, call read_upstream_context() to read parent sources.
3. Generate the function (output it in a ```python block), then call build_and_run_gist(generated_function_source) to test it in a sandbox.
4. If the gist fails, fix the function and call build_and_run_gist again.
5. Call validate_output(result_repr, expected_type_name) to confirm the return type.
Output the final function in a ```python code block."""


_semi_agent: Optional[Any] = None


def get_semi_agent() -> Any:
    """Return the lazy-created pydantic_ai agent (OpenRouter + tools)."""
    global _semi_agent
    if _semi_agent is None:
        _semi_agent = _create_agent()
    return _semi_agent
