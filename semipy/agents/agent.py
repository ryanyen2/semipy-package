"""
Agentic generate-validate-retry loop for semi() function generation.

Uses pydantic_ai Agent (OpenRouter + tools). Builds user prompt from GenerationSpec,
runs agent with streaming, validates (AST, type, execution), retries with feedback on failure.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any, Callable, List, Optional


def _run_async(coro: Any) -> Any:
    """Run a coroutine, compatible with both normal Python and Jupyter (already-running event loop).

    When no event loop is running, uses asyncio.run(). When a loop is already running
    (e.g. in Jupyter), runs the coroutine in a separate thread with its own event loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()

from semipy.agents.compiler import _compile_source
from semipy.agents.config import get_config
from semipy.agents.console_io import (
    confirm,
    generation_progress,
    get_console,
    jupyter_capture_console,
    print_pipeline_log,
    print_reasoning_block,
    print_response_block,
    print_tool_call,
    print_tool_result,
    source_preview,
    decision_description,
    validation_error_panel,
)
from semipy.agents.generator import get_semi_agent
from semipy.agents.gist import GistBuilder
from semipy.agents.executor import GistExecutor
from semipy.models import SemiAgentDeps
from semipy.types import (
    CacheEntry,
    Decision,
    GenerationSpec,
    SemiGenerationError,
    SemiTool,
    ValidationResult,
)
from semipy.agents.profiler import profile_value
from semipy.agents.validator import validate, _extract_function_source


def _handle_stream_event(
    event: Any,
    part_buffers: dict,
    current_part_index: Optional[int],
    current_part_type: Optional[str],
    reasoning_blocks: list,
    verbose: bool,
) -> tuple[Optional[int], Optional[str]]:
    """Dispatch stream event to console; return updated (current_part_index, current_part_type)."""
    from pydantic_ai import (
        PartDeltaEvent,
        PartStartEvent,
        TextPartDelta,
        ThinkingPartDelta,
    )
    from pydantic_ai import FinalResultEvent, FunctionToolCallEvent, FunctionToolResultEvent

    idx = current_part_index
    ptype = current_part_type

    if isinstance(event, PartStartEvent):
        if idx is not None and part_buffers.get(idx):
            content = part_buffers[idx].strip()
            if content and verbose:
                if ptype == "thinking":
                    print_reasoning_block(content)
                    reasoning_blocks.append(content)
                else:
                    print_response_block(content)
        part_buffers.clear()
        idx = event.index
        part_type_name = type(event.part).__name__
        ptype = "thinking" if "Thinking" in part_type_name else "text"
    elif isinstance(event, PartDeltaEvent):
        delta = event.delta
        content = ""
        if isinstance(delta, ThinkingPartDelta):
            content = delta.content_delta or ""
            ptype = "thinking"
        elif isinstance(delta, TextPartDelta):
            content = delta.content_delta or ""
            ptype = "text"
        if content and idx is not None:
            part_buffers.setdefault(idx, "")
            part_buffers[idx] += content
    elif isinstance(event, FinalResultEvent):
        if idx is not None and part_buffers.get(idx) and verbose:
            content = part_buffers[idx].strip()
            if content:
                if ptype == "thinking":
                    print_reasoning_block(content)
                    reasoning_blocks.append(content)
                else:
                    print_response_block(content)
        part_buffers.clear()
        idx = None
        ptype = None
    elif isinstance(event, FunctionToolCallEvent):
        tool_name = getattr(event.part, "tool_name", "?")
        args = getattr(event.part, "args", {})
        args_preview = str(args)[:120] + ("..." if len(str(args)) > 120 else "")
        if verbose:
            print_tool_call(tool_name, args_preview)
    elif isinstance(event, FunctionToolResultEvent):
        result = event.result
        content_preview = str(getattr(result, "content", ""))[:200]
        if len(str(getattr(result, "content", ""))) > 200:
            content_preview += "..."
        tool_name = getattr(result, "tool_name", "?")
        if verbose:
            print_tool_result(tool_name, content_preview, success=True)

    return (idx, ptype)


class SemiAgent:
    """Generates a Python function from a semantic prompt via pydantic_ai Agent (OpenRouter + tools)."""

    def __init__(
        self,
        max_retries: Optional[int] = None,
        enable_execution_test: Optional[bool] = None,
        verbose: Optional[bool] = None,
        stream: Optional[bool] = None,
        confirm_on_failure: Optional[bool] = None,
        confirm_on_external_tools: Optional[bool] = None,
        tools: Optional[List[SemiTool]] = None,
    ):
        config = get_config()
        self.max_retries = max_retries if max_retries is not None else config.max_retries
        self.enable_execution_test = (
            enable_execution_test if enable_execution_test is not None else config.enable_execution_test
        )
        self.verbose = verbose if verbose is not None else config.verbose
        self.stream = stream if stream is not None else config.stream
        self.confirm_on_failure = (
            confirm_on_failure if confirm_on_failure is not None else config.confirm_on_failure
        )
        self.confirm_on_external_tools = (
            confirm_on_external_tools
            if confirm_on_external_tools is not None
            else config.confirm_on_external_tools
        )
        self.tools: List[SemiTool] = list(tools) if tools is not None else []

    def _describe_value(self, name: str, value: Any) -> str:
        return profile_value(name, value)

    def _describe_context(self, spec: GenerationSpec) -> str:
        # In the new architecture, runtime values are passed to the generated function via
        # scaffold slot proxies. We only provide lightweight hints here; the agent can call
        # get_runtime_data_context() if it needs deeper structure.
        if not spec.sample_input or not isinstance(spec.sample_input, dict):
            return ""
        args = spec.sample_input.get("args", ()) or ()
        parts: list[str] = []
        if isinstance(args, (list, tuple)) and args:
            parts.append("Argument types:")
            for i, v in enumerate(args):
                parts.append(f"  - arg{i}: {type(v).__name__}")
        return "\n".join(parts) if parts else ""

    def _build_user_prompt(self, spec: GenerationSpec) -> str:
        def _expected_str() -> str:
            exp = spec.expected_type
            if exp is type(None):
                return "any"
            if exp is callable:
                return "callable"
            if isinstance(exp, type):
                return exp.__name__
            return repr(exp)

        parts = [
            "Implement a single Python function that satisfies this request:",
            "",
            spec.prompt,
            "",
            "Constraints:",
            f"- Return type must be: {_expected_str()}",
        ]

        if spec.decision == Decision.ADAPT and spec.parent_sources:
            parts.append("")
            parts.append("Adapt from this previous implementation (same structure, new parameters):")
            parts.append("```python")
            parts.append(spec.parent_sources[0].strip())
            parts.append("```")
            if spec.lineage_summary:
                parts.append("")
                parts.append("Lineage: " + spec.lineage_summary.replace("\n", " "))

        slot_block = ""
        if spec.slot_spec:
            s = spec.slot_spec
            slot_block += f"Slot category: {s.expected_category.value}\n"
            if s.free_variables:
                slot_block += f"Slot inputs (positional arg order): {s.free_variables}\n"
            if s.output_names:
                slot_block += f"Output names: {s.output_names}\n"
            if s.formal_constraints:
                slot_block += "Hard constraints (must preserve verbatim):\n"
                slot_block += "\n".join(f"  {line}" for line in s.formal_constraints)
                slot_block += "\n"
            if spec.scaffold_source:
                slot_block += "Scaffold context (surrounding formal code):\n"
                slot_block += f"```python\n{spec.scaffold_source}\n```\n"

        if slot_block:
            parts.append("")
            parts.append(slot_block.rstrip())

        context_block = self._describe_context(spec)
        if context_block:
            parts.append("")
            parts.append(context_block)

        if getattr(spec, "downstream_requirements", None):
            reqs = spec.downstream_requirements
            if isinstance(reqs, dict) and reqs:
                parts.append("")
                parts.append("Downstream requirements (your output will be consumed by operations that need):")
                for k, v in reqs.items():
                    parts.append(f"  - {k}: {v}")

        if getattr(spec, "upstream_lineage", None):
            lineage = spec.upstream_lineage
            if lineage:
                parts.append("")
                parts.append("Upstream dependency context: this step consumes output from prior steps in the pipeline.")

        return "\n".join(parts)

    def _build_named_user_prompt(self, spec: GenerationSpec) -> str:
        # Named semi.* calls are removed in the new lowering architecture.
        return self._build_user_prompt(spec)

    def _build_retry_prompt(
        self,
        spec: GenerationSpec,
        last_source: str,
        result: ValidationResult,
        attempt: int,
    ) -> str:
        base = self._build_user_prompt(spec)
        parts = [
            base,
            "\n\nPrevious attempt failed validation:",
            result.error_message,
            "\n\nFix the function and output a corrected version in a ```python block.",
        ]
        if last_source.strip():
            parts.insert(
                -1,
                "\n\nRejected code (fix the issues above):\n```python\n" + last_source.strip() + "\n```",
            )
        return "".join(parts)

    async def generate_async(
        self,
        spec: GenerationSpec,
        user_prompt_override: Optional[str] = None,
    ) -> CacheEntry:
        """Run pydantic_ai agent with streaming; validate and return CacheEntry."""
        config = get_config()
        executor = GistExecutor(
            use_e2b=config.use_e2b,
            timeout=config.gist_timeout,
            e2b_api_key=config.e2b_api_key,
        )
        deps = SemiAgentDeps(
            spec=spec,
            gist_builder=GistBuilder(spec),
            executor=executor,
        )
        prompt = user_prompt_override if user_prompt_override is not None else self._build_user_prompt(spec)
        agent = get_semi_agent()

        if self.verbose:
            decision = spec.decision if spec.decision is not None else Decision.GENERATE
            get_console().print(
                f"[dim][semipy][/] Invoking LLM | decision=[cyan]{decision.value}[/]"
            )

        part_buffers: dict = {}
        current_part_index: Optional[int] = None
        current_part_type: Optional[str] = None
        reasoning_blocks: list = []
        final_output: Optional[str] = None

        try:
            async for event in agent.run_stream_events(prompt, deps=deps):
                current_part_index, current_part_type = _handle_stream_event(
                    event,
                    part_buffers,
                    current_part_index,
                    current_part_type,
                    reasoning_blocks,
                    self.verbose,
                )
                if hasattr(event, "result") and hasattr(event.result, "output"):
                    final_output = getattr(event.result.output, "content", None) or str(event.result.output)
        finally:
            executor = getattr(deps, "executor", None)
            if executor is not None and hasattr(executor, "close_async"):
                await executor.close_async()

        source = getattr(deps, "generated_source", None) or ""
        if not source.strip() and final_output:
            source = _extract_function_source(final_output)
            # print(f"Generated source:\n {source}")
        if not source.strip():
            raise SemiGenerationError("Agent did not produce a Python function (no code block or build_and_run_gist source).")

        result = validate(
            source,
            expected_type=spec.expected_type,
            sample_input=spec.sample_input,
            enable_execution=self.enable_execution_test,
            usage_hint=getattr(spec, "usage_hint", ""),
            spec=spec,
        )
        if not result.passed:
            raise SemiGenerationError(
                result.error_message or "Validation failed.",
                last_source=source,
                last_result=result,
            )

        fn = _compile_source(source)
        reasoning_summary = "\n\n".join(reasoning_blocks[:3]) if reasoning_blocks else None
        tool_calls = getattr(deps, "tool_calls_log", None)
        return CacheEntry(
            generated_source=source,
            compiled_fn=fn,
            expected_type=spec.expected_type,
            reasoning_summary=reasoning_summary,
            tool_calls_made=tool_calls,
        )

    def generate(self, spec: GenerationSpec) -> CacheEntry:
        """Synchronous wrapper: run generate_async in a new event loop."""
        total_attempts = self.max_retries + 1
        decision = spec.decision if spec.decision is not None else Decision.GENERATE

        with jupyter_capture_console(), generation_progress(self.verbose) as progress:
            progress.set_call_site(spec.call_site)
            progress.log_step("Generate")
            progress.log_step(f"Decision: {decision_description(decision)}")
            print_pipeline_log(spec.call_site, "resolve", f"Cache miss. {decision_description(decision)}")
            progress.set_stage("generate")
            progress.update(f"Cache miss. {decision_description(decision)}")
            if self.confirm_on_external_tools and getattr(spec, "require_external_tools", False):
                config = get_config()
                if not confirm(
                    "This may require external tools (web/PDF/image). Continue with current setup?",
                    default_no=True,
                    confirm_callback=config.confirm_callback,
                ):
                    raise SemiGenerationError(
                        "User declined to continue without external tools (require_external_tools=True, confirm_on_external_tools=True)."
                    )

            last_source = ""
            last_result: Optional[ValidationResult] = None

            for attempt in range(total_attempts):
                progress.log_step(f"Generating (attempt {attempt + 1}/{total_attempts})")
                if last_result and (last_result.error_message or ""):
                    print_pipeline_log(spec.call_site, "generate", f"Retry {attempt + 1}/{total_attempts}: fixing validation error")
                    progress.set_stage("generate")
                    progress.update(f"Retrying (attempt {attempt + 1}/{total_attempts}): fixing validation error...")
                    prompt_override = self._build_retry_prompt(spec, last_source, last_result, attempt)
                else:
                    print_pipeline_log(spec.call_site, "generate", f"Calling agent (attempt {attempt + 1}/{total_attempts})")
                    progress.set_stage("generate")
                    progress.update(f"Calling agent (attempt {attempt + 1}/{total_attempts})...")
                    prompt_override = None

                try:
                    entry = _run_async(self.generate_async(spec, user_prompt_override=prompt_override))
                except SemiGenerationError as e:
                    last_source = getattr(e, "last_source", "") or ""
                    last_result = getattr(e, "last_result", None)
                    if attempt + 1 >= total_attempts:
                        progress.record_failure(str(e), validation_result=last_result, source=last_source, call_site=spec.call_site)
                        raise
                    continue

                progress.log_step("Valid")
                progress.record_success(attempt + 1, call_site=spec.call_site)
                return entry

            progress.record_failure(
                last_result.error_message if last_result else "Unknown error",
                validation_result=last_result,
                source=last_source if last_source else None,
                call_site=spec.call_site,
            )
        raise SemiGenerationError(
            f"Failed to generate valid function after {total_attempts} attempts. "
            + (last_result.error_message if last_result else "Unknown error.")
        )
