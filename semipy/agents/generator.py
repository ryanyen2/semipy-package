"""
LLM generation via pydantic_ai Agent + OpenAI.

Agent has tools: get_runtime_data_context, profile_data_and_flow, read_upstream_context,
read_file_context, read_document_context, build_and_run_gist, validate_output. Streaming and reasoning via run_stream_events.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Optional

from pydantic_ai import Agent, RunContext

from semipy.agents.config import get_config
from semipy.agents.profiler import profile_runtime_context
from semipy.documents import load_document_text
from semipy.models import (
    DocumentContextResult,
    FileContextResult,
    GistRunResult,
    OutputValidationResult,
    ProfileDataResult,
    RuntimeDataContextResult,
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
        # openai_text_verbosity='low',
        openai_reasoning_summary='auto',
        openai_send_reasoning_ids=True,
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
    """Lazy creation of pydantic_ai Agent with OpenRouter/OpenAI and tools."""
    config = get_config()
    # Prefer OpenAI when configured; fall back to OpenRouter otherwise.
    # This keeps backend selection predictable when both keys are present in `.env`.
    use_openai = bool(config.openai_api_key) or bool(os.getenv("OPENAI_API_KEY"))
    if use_openai:
        model, settings = _create_openai_model(config)
    else:
        model, settings = _create_openrouter_model(config)
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
            from semipy.agents.refs.example_glm import profile_data_and_flow_impl  # type: ignore[reportMissingImports]
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
    async def get_runtime_data_context(ctx: RunContext[SemiAgentDeps]) -> RuntimeDataContextResult:
        """Get a summary of variables in scope (caller_locals and variable_values): structure, schemas, and value distributions. Call this when you need to understand what data is available (DataFrames, lists, dicts, paths, URLs, etc.) so your implementation works for all values, not just a single sample."""
        try:
            deps = ctx.deps
            spec = getattr(deps, "spec", None)
            if not spec:
                return RuntimeDataContextResult(success=False, error="No spec in deps.")
            sample_input = getattr(spec, "sample_input", None)
            runtime_values: dict[str, Any] = {}
            if isinstance(sample_input, dict):
                rv = sample_input.get("runtime_values", None)
                if isinstance(rv, dict):
                    runtime_values = rv
            locals_dict: dict[str, Any] = {}
            variable_values = runtime_values or None
            summary = profile_runtime_context(
                locals_dict,
                variable_values=variable_values,
                total_budget=12000,
                collection_budget=7000,
            )
            obs = getattr(spec, "session_input_observations", None)
            if isinstance(obs, dict) and obs:
                extra = ["\nSession input observations (distinct values seen for this slot):"]
                for k in sorted(obs.keys()):
                    v = obs[k]
                    if isinstance(v, list):
                        extra.append(f"  {k}: {v}")
                summary = summary + "\n" + "\n".join(extra)
            return RuntimeDataContextResult(success=True, summary=summary)
        except Exception as e:
            return RuntimeDataContextResult(success=False, error=str(e))

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
    async def list_library_primitives(ctx: RunContext[SemiAgentDeps]) -> str:
        """Return the available library primitives for this request (if any). Same as the block in the user prompt."""
        deps = ctx.deps
        spec = getattr(deps, "spec", None)
        ctx_block = getattr(spec, "library_context", None) if spec else None
        return (ctx_block or "").strip() or "No library primitives for this request."

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
    async def read_document_context(
        ctx: RunContext[SemiAgentDeps],
        file_path: str,
        chunk_index: int = 0,
        chunk_size: int = 12000,
        layout_heavy: bool = False,
        backend: str = "auto",
    ) -> DocumentContextResult:
        """Load document text: UTF-8 for text files; PDFs via internal load_document_text (liteparse and/or LlamaCloud). Set layout_heavy for layout-heavy PDFs. backend: auto, liteparse, or llama_cloud. Large bodies are chunked; increase chunk_index for more."""
        try:
            p = Path(file_path).expanduser().resolve()
            if not p.exists():
                return DocumentContextResult(
                    success=False,
                    error=f"File not found: {file_path}",
                )
            bk = backend if backend in ("auto", "liteparse", "llama_cloud") else "auto"
            source_kind = "pdf" if p.suffix.casefold() == ".pdf" else "text"
            try:
                full_text = load_document_text(
                    p,
                    backend=bk,
                    layout_heavy=layout_heavy,
                )
            except Exception as e:
                return DocumentContextResult(
                    success=False,
                    error=str(e),
                    source_kind=source_kind,
                )
            size = max(1000, int(chunk_size))
            if not full_text:
                return DocumentContextResult(
                    success=True,
                    content="",
                    page_count=None,
                    chunk_index=0,
                    total_chunks=1,
                    source_kind=source_kind,
                )
            total_chunks = max(1, math.ceil(len(full_text) / size))
            idx = max(0, min(int(chunk_index), total_chunks - 1))
            start = idx * size
            chunk = full_text[start : start + size]
            return DocumentContextResult(
                success=True,
                content=chunk,
                page_count=None,
                chunk_index=idx,
                total_chunks=total_chunks,
                source_kind=source_kind,
            )
        except Exception as e:
            return DocumentContextResult(success=False, error=str(e))

    @agent.tool
    async def build_and_run_gist(ctx: RunContext[SemiAgentDeps], generated_function_source: str) -> GistRunResult:
        """Assemble a minimal runnable gist (user context + generated function), execute in sandbox, return stdout/stderr/result. Call this to test your generated function."""
        deps = ctx.deps
        gist_builder = getattr(deps, "gist_builder", None)
        executor = getattr(deps, "executor", None)
        if not gist_builder or not executor:
            return GistRunResult(success=False, error="GistBuilder or Executor not in deps")
        gist = gist_builder.build(generated_function_source)
        if not gist:
            detail = getattr(gist_builder, "last_build_error", None) or ""
            msg = "Could not build gist (missing user source or AST trace failed)."
            if detail:
                msg = f"{msg} {detail}"
            return GistRunResult(success=False, error=msg)
        result = await executor.execute_async(gist.source, user_source_path=gist.user_source_path)
        deps.generated_source = generated_function_source
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
                # For parameterized collection types like list[EventTemplate],
                # check that the repr does not look like a plain list of dicts.
                # A proper class instance repr starts with ClassName(...), not {.
                if expected_type_name.startswith("list[") and s.startswith("["):
                    try:
                        # Extract the element class name from "list[ClassName]"
                        inner = expected_type_name[5:-1].strip().split(".")[-1]
                        stripped = s.lstrip("[").lstrip()
                        # Plain dict elements start with { ; class instance reprs start with ClassName(
                        if stripped.startswith("{"):
                            return OutputValidationResult(
                                valid=False,
                                message=(
                                    f"Result is a list of plain dicts, but expected list[{inner}]. "
                                    f"Each element must be a {inner}(...) instance, not a dict."
                                ),
                                expected_type=expected_type_name,
                                actual_type="list[dict]",
                            )
                    except Exception:
                        pass
                return OutputValidationResult(
                    valid=True,
                    message=f"Cannot fully validate type {expected_type_name}; structural check passed.",
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
- Parameters: the pipeline passes slot inputs as positional arguments in the exact order listed in the user prompt ("Slot inputs (positional arg order): [...]"). The function MUST have that many positional parameters (use *args only if the prompt explicitly allows variadic input). Parameter names are yours, but arity and order must match. If one slot input is a structured object (e.g. a dataclass row), take that single parameter and read its attributes inside the function; do not split it into extra parameters unless each piece is a separate slot input.
- Gist sandbox calls may pass None for non-primitive arguments (e.g. a method's `self` placeholder). Do not assume a real instance; use only the primitive parameters you need, or branch on None and still return a valid value for validation.
- Your function is invoked repeatedly (once per row, per element, or per item). The first argument is one value from a column or collection. Implement so the function works for every possible value in that column/collection, not only the single example you may see in get_runtime_data_context. Do not hardcode the sample value (e.g. one date or one country name).
- Return type: match exactly what the user needs (bool for conditions, str for text, int/float for numbers, or the described type). Prefer a typed signature when the return type is known (e.g. def f(row, c3) -> bool:). The pipeline preserves type annotations.
- Handle edge cases: None, missing keys, empty data. Prefer safe defaults over raising.
- Use the data context (variable_values, sample_input, get_runtime_data_context) when provided; use actual column names and value distributions from the context. Never fabricate data values. Use the full value_distribution / distinct_sample to support all values, not just one.
- When a usage context is provided (e.g. "passed as argument to X"), return the type that X expects.
- Do not use emoji or decorative output. No docstrings or comments in the code, just code.
- Do not call `print()` or include any sample/test invocation code inside the generated function. Return only the requested value.
- When the user provides a previous implementation (adapt or inspiration), preserve its structure where possible and change only what is needed.
- When the prompt includes scaffold context (surrounding user code), it may use placeholders such as `...` or informal spec comments. Implement the slot fully in your generated function; the scaffold is context for intent, not a requirement that your generated body match the user file line-for-line.
- When "Available library primitives" are shown in the prompt, you may reuse or adapt them to satisfy the request.

Slot category instructions (from SlotSpec):
- EXPRESSION: generate def __slot_N__(arg1, arg2, ...) that returns a single value matching expected_type. The returned value replaces the slot call in the scaffold.
- If the slot has zero inputs (no free variables), the signature must be `def __slot_N__(): ...` with no required positional parameters.
- STATEMENT_BLOCK: generate def __slot_N__(arg1, arg2, ...) that returns a dict[str, Any] with exactly the keys: output_names. Each key maps to the value that the key name should bind. If a value is a list of structured rows and the scaffold defines a matching @dataclass (same field names), each row must be an instance of that class, not a plain dict, so downstream isinstance checks succeed.
- FUNCTION_BODY: implement the full function body as today; return expected_type.
- If expected_type is `callable`, your function must return another callable (a factory). If expected_type is a domain class, construct and return an instance of that class.
- If expected_type is `list[SomeClass]` (or another collection parameterized with a user-defined class), each element MUST be an instance of that class constructed via its constructor. Import the class from the appropriate module. Returning a list of plain dicts will fail type validation even if the dict keys match the class fields — validation checks isinstance, not just pydantic coercibility.
- Hard constraints (must preserve): lines provided as formal_constraints MUST appear verbatim in your implementation. Do not remove or modify them.

Tool usage:
1. When the prompt refers to data (e.g. a column, a table, a file, a URL), call get_runtime_data_context() first. Tables/collections are listed first with full column value distributions; any scalar is labeled as one sample. Your implementation must work for all values in the data (all dates, all countries, etc.), not just that sample. Use this for any domain (tables, PDFs, URLs, dicts, etc.).
2. If the spec contains data variables or sample data, you may call profile_data_and_flow(code, working_dir?) to get data profile and flow.
3. If adapting from a parent, call read_upstream_context() to read parent sources.
4. If the prompt references a PDF or document on disk, call read_document_context(file_path, chunk_index=0, chunk_size=...) and, if total_chunks>1, additional chunks as needed.
5. If library primitives are provided, you may call list_library_primitives() to see them again.
6. Generate the function, then call build_and_run_gist(generated_function_source) with the raw function source only: the exact "def name(...):" and body as a string. No markdown, no ```python wrapper, no surrounding text.
7. If the gist fails (success=False), read stderr and fix the function; call build_and_run_gist again with the corrected source.
8. Output the final function in a ```python code block."""
# 7. When the gist succeeds, call validate_output(result_repr, expected_type_name) with the result_repr from the gist tool result (and expected_type_name from the user request, e.g. "bool", "list", "str").


_semi_agent: Optional[Agent[SemiAgentDeps]] = None


def get_semi_agent() -> Agent[SemiAgentDeps]:
    global _semi_agent
    if _semi_agent is None:
        _semi_agent = _create_agent()
    return _semi_agent
