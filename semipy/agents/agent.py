"""
Agentic generate-validate-retry loop for semi() function generation.

Uses pydantic_ai Agent (OpenRouter + tools). Builds user prompt from GenerationSpec,
runs agent with streaming, validates (AST, type, execution), retries with feedback on failure.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import threading
from typing import Any, Callable, List, Optional


_async_loop_lock = threading.Lock()
_async_loop: asyncio.AbstractEventLoop | None = None
_async_loop_thread: threading.Thread | None = None


def _ensure_async_loop() -> asyncio.AbstractEventLoop:
    """Create or return the shared background event loop."""
    global _async_loop, _async_loop_thread
    with _async_loop_lock:
        if (
            _async_loop is not None
            and not _async_loop.is_closed()
            and _async_loop_thread is not None
            and _async_loop_thread.is_alive()
        ):
            return _async_loop

        loop_ready = threading.Event()
        loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

        def _loop_thread_main() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_holder["loop"] = loop
            loop_ready.set()
            loop.run_forever()

        thread = threading.Thread(target=_loop_thread_main, name="semipy-async-runner", daemon=True)
        thread.start()
        loop_ready.wait(timeout=5.0)
        loop = loop_holder.get("loop")
        if loop is None:
            raise RuntimeError("Failed to initialize shared async loop")
        _async_loop = loop
        _async_loop_thread = thread
        return loop


def _run_async(coro: Any) -> Any:
    """Run a coroutine on a persistent background event loop.

    Using a long-lived loop avoids teardown races from creating/closing event loops
    for every slot invocation, which can surface as ``RuntimeError: Event loop is closed``
    during streamed HTTP client shutdown.
    """
    loop = _ensure_async_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result()

from semipy.agents.compiler import _compile_source
from semipy.agents.config import (
    STREAM_PEEK_LINES,
    STREAM_SHOW_ELAPSED,
    STREAM_TIMELINE,
    effective_stream_display_mode,
    get_config,
)
from semipy.agents.console_messages import format_tool_call_line, format_tool_result_line
from semipy.agents.console_view import GenerationStreamView, JupyterStreamPeek
from semipy.agents.console_io import (
    _is_jupyter,
    generation_progress,
    get_console,
    jupyter_capture_console,
    print_pipeline_log,
    print_reasoning_block,
    print_response_block,
    print_tool_intent_line,
    print_tool_outcome_line,
    print_friendly_exception,
    source_preview,
    pipeline_generate_status,
    pipeline_resolution_message,
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
    SlotCategory,
    ValidationResult,
)
from semipy.agents.profiler import profile_runtime_context, profile_value
from semipy.agents.validator import (
    _extract_function_source,
    _should_use_typeadapter_for_expected_type,
    validate,
)


def _tool_args_dict(part: Any) -> dict[str, Any]:
    if hasattr(part, "args_as_dict"):
        try:
            d = part.args_as_dict()
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _pipeline_trace_enabled() -> bool:
    """Full prompt / tools / reasoning dump; controlled only via env (not SemiConfig)."""
    return os.getenv("SEMIPY_PIPELINE_TRACE", "").strip().lower() in ("1", "true", "yes")


def _trace_tool_result_snippet(content: Any, limit: int = 2000) -> str:
    try:
        s = repr(content)
    except Exception:
        s = "<unrepr>"
    if len(s) > limit:
        return s[: limit - 3] + "..."
    return s


def _handle_stream_event(
    event: Any,
    part_buffers: dict,
    current_part_index: Optional[int],
    current_part_type: Optional[str],
    reasoning_blocks: list,
    *,
    verbose: bool,
    stream_mode: str,
    stream_sink: Optional[Any],
    deps: Optional[Any] = None,
    pipeline_trace: bool = False,
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
    show_tool_lines = verbose or pipeline_trace
    debug_tools = pipeline_trace
    out_console = stream_sink.console if stream_sink is not None else get_console()
    show_names = pipeline_trace
    capture_reasoning = verbose or pipeline_trace

    if isinstance(event, PartStartEvent):
        if idx is not None and part_buffers.get(idx):
            content = part_buffers[idx].strip()
            if content:
                if ptype == "thinking" or ptype == "reasoning":
                    if verbose and stream_mode == "full":
                        print_reasoning_block(content)
                    elif verbose and stream_mode == "peek" and content:
                        # Peek mode streams a rolling tail; also print completed reasoning parts.
                        print_reasoning_block(content)
                    if capture_reasoning:
                        reasoning_blocks.append(content)
                else:
                    if verbose and stream_mode == "full":
                        print_response_block(content)
        part_buffers.clear()
        if stream_sink is not None:
            stream_sink.clear_stream_buffer()
            stream_sink.set_active_phase("Model")
        idx = event.index
        part_type_name = type(event.part).__name__
        ptype = "thinking" if "Thinking" in part_type_name else "text"
    elif isinstance(event, PartDeltaEvent):
        idx = event.index
        delta = event.delta
        content = ""
        if isinstance(delta, ThinkingPartDelta):
            content = delta.content_delta or ""
            ptype = "thinking"
        elif isinstance(delta, TextPartDelta):
            content = delta.content_delta or ""
            ptype = "text"
        else:
            content = ""
        if content:
            part_buffers.setdefault(idx, "")
            part_buffers[idx] += content
            if stream_mode == "peek" and stream_sink is not None:
                stream_sink.append_stream_delta(content, kind="thinking" if ptype == "thinking" else "output")
    elif isinstance(event, FinalResultEvent):
        if idx is not None and part_buffers.get(idx):
            content = part_buffers[idx].strip()
            if content:
                if ptype == "thinking":
                    if verbose and stream_mode == "full":
                        print_reasoning_block(content)
                    elif verbose and stream_mode == "peek" and content:
                        print_reasoning_block(content)
                    if capture_reasoning:
                        reasoning_blocks.append(content)
                else:
                    if verbose and stream_mode == "full":
                        print_response_block(content)
        part_buffers.clear()
        idx = None
        ptype = None
        if stream_sink is not None:
            stream_sink.set_active_phase("Validate")
    elif isinstance(event, FunctionToolCallEvent):
        tool_name = getattr(event.part, "tool_name", "?")
        args = _tool_args_dict(event.part)
        if deps is not None:
            try:
                deps.tool_calls_log.append(f"call:{tool_name} {json.dumps(args, default=str)}")
            except Exception:
                deps.tool_calls_log.append(f"call:{tool_name} args=(unserializable)")
        intent = format_tool_call_line(tool_name, args, debug=debug_tools)
        if show_tool_lines:
            print_tool_intent_line(
                tool_name,
                intent,
                console=out_console,
                show_tool_name=show_names,
            )
        if stream_sink is not None:
            stream_sink.set_active_phase("Tools")
    elif isinstance(event, FunctionToolResultEvent):
        tr = event.result
        tool_name = getattr(tr, "tool_name", "?")
        content = getattr(tr, "content", None)
        if deps is not None and pipeline_trace:
            deps.tool_calls_log.append(f"result:{tool_name} {_trace_tool_result_snippet(content)}")
        outcome, ok = format_tool_result_line(tool_name, content, debug=debug_tools)
        if show_tool_lines:
            print_tool_outcome_line(outcome, ok, console=out_console)
        if stream_sink is not None:
            stream_sink.set_active_phase("Model")

    return (idx, ptype)


class SemiAgent:
    """Generates a Python function from a semantic prompt via pydantic_ai Agent (OpenRouter + tools)."""

    def __init__(
        self,
        max_retries: Optional[int] = None,
        verbose: Optional[bool] = None,
        tools: Optional[List[SemiTool]] = None,
    ):
        config = get_config()
        self.max_retries = max_retries if max_retries is not None else config.max_retries
        self.verbose = verbose if verbose is not None else config.verbose
        self.tools: List[SemiTool] = list(tools) if tools is not None else []

    def _describe_value(self, name: str, value: Any) -> str:
        return profile_value(name, value)

    def _describe_context(self, spec: GenerationSpec) -> str:
        # Provide deterministic profiling of runtime values directly in the prompt.
        # This reduces reliance on the model deciding when to call tools.
        if not spec.sample_input or not isinstance(spec.sample_input, dict):
            return ""
        runtime_values = spec.sample_input.get("runtime_values", None)
        if not isinstance(runtime_values, dict) or not runtime_values:
            return ""
        return "Runtime data profile:\n" + profile_runtime_context(
            locals_dict={},
            variable_values=runtime_values,
            total_budget=12000,
            collection_budget=7000,
        )

    def _describe_session_input_observations(self, spec: GenerationSpec) -> str:
        obs = getattr(spec, "session_input_observations", None)
        if not isinstance(obs, dict) or not obs:
            return ""
        lines = [
            "Distinct slot input values observed across this session (bounded list per parameter):",
        ]
        for k in sorted(obs.keys()):
            v = obs[k]
            if isinstance(v, list):
                lines.append(f"  - {k}: {v}")
        return "\n".join(lines)

    def _build_user_prompt(self, spec: GenerationSpec) -> str:
        def _expected_str() -> str:
            exp = spec.expected_type
            if exp is type(None):
                return "any"
            if exp is callable:
                return "callable"
            if isinstance(exp, type):
                # Fully qualify user-defined/domain types so the model can import the exact
                # class identity (important for isinstance-based validation).
                if exp.__module__ not in ("builtins", "typing"):
                    return f"{exp.__module__}.{exp.__name__}"
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

        # For domain objects, we must construct the exact class identity expected by
        # isinstance(result, expected_type) during validation.
        if isinstance(spec.expected_type, type) and spec.expected_type.__module__ not in ("builtins", "typing"):
            exp = spec.expected_type
            parts.append(
                f"- Expected domain object type is {exp.__module__}.{exp.__name__}. "
                "Import that exact class in the generated function and return an instance of it."
            )

        if spec.decision == Decision.ADAPT and spec.parent_sources:
            parts.append("")
            parts.append(
                "The previous implementation below FAILED runtime verification for the current input. "
                "Adapt it: keep all existing format/branch handling that works, "
                "and add or fix only what is needed for the failing input. "
                "Do not remove branches that handle other formats."
            )
            if spec.verify_failure_context:
                parts.append("")
                parts.append("Verification failure reason:")
                parts.append(spec.verify_failure_context)
                parts.append("")
                parts.append(
                    "Use this failure reason to understand what went wrong. "
                    "If the error indicates a type mismatch, fix the return type. "
                    "If the error indicates an execution failure (exception, empty output, "
                    "identity return), fix the logic for the new input shape while "
                    "preserving handling of previously working inputs."
                )
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
                slot_block += (
                    f"The generated function must accept exactly {len(s.free_variables)} "
                    "positional parameters in this order (names may differ).\n"
                )
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

        if (
            spec.slot_spec
            and spec.slot_spec.expected_category == SlotCategory.STATEMENT_BLOCK
            and spec.slot_spec.output_names
            and len(spec.slot_spec.output_names) == 1
            and _should_use_typeadapter_for_expected_type(spec.expected_type)
        ):
            parts.append("")
            parts.append(
                "STATEMENT_BLOCK contract: return a dict with exactly one key "
                f"{spec.slot_spec.output_names[0]!r}. Its value must validate as "
                f"{spec.expected_type!r} (pydantic TypeAdapter): use the real field names for that "
                "type, including nested list elements and enums."
            )

        context_block = self._describe_context(spec)
        if context_block:
            parts.append("")
            parts.append(context_block)

        obs_block = self._describe_session_input_observations(spec)
        if obs_block:
            parts.append("")
            parts.append(obs_block)

        if getattr(spec, "runtime_profile_scalar_only", False):
            parts.append("")
            parts.append(
                "The current invocation only exposes scalar slot inputs (no table or collection in scope). "
                "Assume each invocation may pass a different shape or format; do not hardcode "
                "behavior to a single sample value."
            )

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

    def _make_stream_sink(
        self,
    ) -> Optional[GenerationStreamView | JupyterStreamPeek]:
        """Peek UI: Rich Live + timeline in terminal; throttled panels in Jupyter."""
        if not (self.verbose and effective_stream_display_mode(verbose=self.verbose) == "peek"):
            return None
        if _is_jupyter():
            return JupyterStreamPeek(get_console(), STREAM_PEEK_LINES)
        view = GenerationStreamView(
            get_console(),
            STREAM_PEEK_LINES,
            enabled=True,
            show_timeline=STREAM_TIMELINE,
        )
        view.set_show_elapsed(STREAM_SHOW_ELAPSED)
        return view

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
        stream_sink: Optional[GenerationStreamView | JupyterStreamPeek] = None,
    ) -> CacheEntry:
        """Run pydantic_ai agent with streaming; validate and return CacheEntry."""
        config = get_config()
        pipeline_trace = _pipeline_trace_enabled()
        stream_mode = effective_stream_display_mode(verbose=self.verbose)
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

        if (self.verbose or pipeline_trace) and stream_sink is None:
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
                    verbose=self.verbose,
                    stream_mode=stream_mode,
                    stream_sink=stream_sink,
                    deps=deps,
                    pipeline_trace=pipeline_trace,
                )
                if hasattr(event, "result") and hasattr(event.result, "output"):
                    final_output = getattr(event.result.output, "content", None) or str(event.result.output)
        finally:
            executor = getattr(deps, "executor", None)
            if executor is not None and hasattr(executor, "close_async"):
                await executor.close_async()

        if pipeline_trace:
            decision = spec.decision if spec.decision is not None else Decision.GENERATE
            print(f"[semipy.pipeline_trace] decision={decision!r}")
            print(f"[semipy.pipeline_trace] user_prompt:\n{prompt}")
            if reasoning_blocks:
                print(
                    "[semipy.pipeline_trace] reasoning (first blocks):\n"
                    + "\n\n".join(reasoning_blocks[:3])
                )
            else:
                print(
                    "[semipy.pipeline_trace] reasoning: (no thinking/reasoning parts in this stream; "
                    "depends on provider/model. OpenRouter may expose these as separate message types.)"
                )
            if deps.tool_calls_log:
                print("[semipy.pipeline_trace] tool_calls:\n" + "\n".join(deps.tool_calls_log))

        source = getattr(deps, "generated_source", None) or ""
        if not source.strip() and final_output:
            source = _extract_function_source(final_output)
            # print(f"Generated source:\n {source}")
        if not source.strip():
            raise SemiGenerationError("Agent did not produce a Python function (no code block or build_and_run_gist source).")

        if stream_sink is not None:
            stream_sink.set_active_phase("Validate")

        result = validate(
            source,
            expected_type=spec.expected_type,
            sample_input=spec.sample_input,
            enable_execution=True,
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
        if stream_sink is not None:
            stream_sink.set_active_phase("Done")
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
        # Inline placeholder slots (`... #> ...`) are intended to be a direct
        # one-shot fill for the missing expression/condition. Retrying tends to
        # overfit and churn on tiny contexts, so keep them single-pass.
        slot = getattr(spec, "slot_spec", None)
        if slot is not None:
            hints = set(getattr(slot, "usage_hints", []) or [])
            if "inline:assign" in hints:
                total_attempts = 1
        decision = spec.decision if spec.decision is not None else Decision.GENERATE
        use_peek_sink = self.verbose and effective_stream_display_mode(verbose=self.verbose) == "peek"

        with jupyter_capture_console(), generation_progress(
            self.verbose,
            use_status_line=not use_peek_sink,
        ) as progress:
            progress.set_call_site(spec.call_site)
            progress.log_step("Generate")
            progress.log_step(f"Decision: {pipeline_resolution_message(decision)}")
            print_pipeline_log(spec.call_site, "resolve", pipeline_resolution_message(decision))
            progress.set_stage("generate")
            progress.update(pipeline_resolution_message(decision))
            last_source = ""
            last_result: Optional[ValidationResult] = None

            for attempt in range(total_attempts):
                progress.log_step(f"Generating (attempt {attempt + 1}/{total_attempts})")
                if last_result and (last_result.error_message or ""):
                    print_pipeline_log(
                        spec.call_site,
                        "generate",
                        pipeline_generate_status(attempt + 1, total_attempts, retry=True),
                    )
                    progress.set_stage("generate")
                    progress.update(pipeline_generate_status(attempt + 1, total_attempts, retry=True))
                    prompt_override = self._build_retry_prompt(spec, last_source, last_result, attempt)
                else:
                    print_pipeline_log(
                        spec.call_site,
                        "generate",
                        pipeline_generate_status(attempt + 1, total_attempts, retry=False),
                    )
                    progress.set_stage("generate")
                    progress.update(pipeline_generate_status(attempt + 1, total_attempts, retry=False))
                    prompt_override = None

                stream_sink = self._make_stream_sink()
                if stream_sink is not None:
                    stream_sink.__enter__()
                try:
                    entry = _run_async(
                        self.generate_async(
                            spec,
                            user_prompt_override=prompt_override,
                            stream_sink=stream_sink,
                        )
                    )
                except SemiGenerationError as e:
                    last_source = getattr(e, "last_source", "") or ""
                    last_result = getattr(e, "last_result", None)
                    if attempt + 1 >= total_attempts:
                        progress.record_failure(str(e), validation_result=last_result, source=last_source, call_site=spec.call_site)
                        if self.verbose:
                            print_friendly_exception(e, title="Generation failed")
                        raise
                    continue
                finally:
                    if stream_sink is not None:
                        stream_sink.__exit__(None, None, None)

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
