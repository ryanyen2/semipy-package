"""
Agentic generate-validate-retry loop for semi() function generation.

Builds user and system prompts from GenerationSpec, calls the generator,
validates (AST, type, execution), and retries with validation feedback on failure.
"""
from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

from semipy.compiler import _compile_source
from semipy.config import get_config
from semipy.console_io import (
    confirm,
    generation_progress,
    get_console,
    print_pipeline_log,
    source_preview,
    decision_description,
    validation_error_panel,
)
from semipy.generator import SemiGenerator, SYSTEM_PROMPT
from semipy.tools import inject_tools_into_system_prompt, parse_tool_refs
from semipy.types import (
    CacheEntry,
    Decision,
    GenerationSpec,
    SemiGenerationError,
    SemiTool,
    ValidationResult,
)
from semipy.profiler import profile_value
from semipy.validator import validate, _extract_function_source


class SemiAgent:
    """Generates a Python function from a semantic prompt, with validation and retries."""

    def __init__(
        self,
        generator: Optional[SemiGenerator] = None,
        max_retries: Optional[int] = None,
        enable_execution_test: Optional[bool] = None,
        verbose: Optional[bool] = None,
        stream: Optional[bool] = None,
        confirm_on_failure: Optional[bool] = None,
        confirm_on_external_tools: Optional[bool] = None,
        tools: Optional[List[SemiTool]] = None,
    ):
        config = get_config()
        self.generator = generator or SemiGenerator()
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
        """Data-agnostic introspection via duck typing; delegates to profiler."""
        return profile_value(name, value)

    def _describe_context(self, spec: GenerationSpec) -> str:
        """Aggregate data context: variable_values descriptions, type_hints, trimmed source."""
        parts: list[str] = []
        if spec.variable_values:
            for n, v in spec.variable_values.items():
                parts.append(self._describe_value(n, v))
        if spec.context:
            if spec.context.type_hints:
                parts.append("Type hints: " + str(spec.context.type_hints))
            if spec.context.source_code:
                lines = spec.context.source_code.strip().splitlines()
                trimmed = lines[:30] if len(lines) > 30 else lines
                parts.append("Source (excerpt):\n" + "\n".join(trimmed))
        if not parts:
            return ""
        return "Data context:\n" + "\n\n".join(parts)

    def _build_user_prompt(self, spec: GenerationSpec) -> str:
        parts = [
            "Implement a single Python function that satisfies this request:",
            "",
            spec.prompt,
            "",
            "Constraints:",
            f"- Return type must be: {spec.expected_type.__name__ if spec.expected_type is not type(None) else 'any'}",
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
        if spec.decision == Decision.FORK and spec.parent_sources:
            parts.append("")
            parts.append("Use as inspiration (structure has changed):")
            parts.append("```python")
            parts.append(spec.parent_sources[0].strip())
            parts.append("```")
        if spec.template and spec.template.variable_names and not spec.method_name:
            n = len(spec.template.variable_names)
            parts.append(
                f"- The function will be called with exactly {n} positional arguments (in order). "
                "The first argument is the value that changes per invocation; the rest are fixed context for this call."
            )
        context_block = self._describe_context(spec)
        if context_block:
            parts.append("")
            parts.append(context_block)
        if getattr(spec, "usage_hint", ""):
            parts.append(f"- Usage context: the result will be {spec.usage_hint}")
            if "FormatStrFormatter" in spec.usage_hint:
                parts.append(
                    "- The result is passed to matplotlib FormatStrFormatter: use a Python % format (e.g. %.1f, %d), not strftime (e.g. %Y-%m-%d)."
                )
        if spec.sample_input:
            parts.append("- Sample input (the function will be called with these argument types):")
            parts.append(json.dumps(spec.sample_input, default=repr, indent=2))
        if spec.constant_values:
            parts.append("- Constant context (use as parameters after the first, or bake into the function):")
            parts.append(json.dumps(spec.constant_values, default=repr, indent=2))
        return "\n".join(parts)

    def _build_named_user_prompt(self, spec: GenerationSpec) -> str:
        """Prompt for semi.name(...): function name is the specification; describe args by position/type/sample."""
        parts = [
            spec.prompt,
            "",
            "Constraints:",
            f"- Return type must be: {spec.expected_type.__name__ if spec.expected_type is not type(None) else 'any'}",
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
        if spec.decision == Decision.FORK and spec.parent_sources:
            parts.append("")
            parts.append("Use as inspiration (structure has changed):")
            parts.append("```python")
            parts.append(spec.parent_sources[0].strip())
            parts.append("```")
        context_block = self._describe_context(spec)
        if context_block:
            parts.append("")
            parts.append(context_block)
        if getattr(spec, "usage_hint", ""):
            parts.append(f"- Usage context: the result will be {spec.usage_hint}")
            if "FormatStrFormatter" in spec.usage_hint:
                parts.append(
                    "- The result is passed to matplotlib FormatStrFormatter: use a Python % format (e.g. %.1f, %d), not strftime (e.g. %Y-%m-%d)."
                )
        if spec.sample_input:
            parts.append("- Sample input (the function will be called with these argument types):")
            parts.append(json.dumps(spec.sample_input, default=repr, indent=2))
        if spec.constant_values:
            parts.append("- Constant context (bake into the function or add as parameters):")
            parts.append(json.dumps(spec.constant_values, default=repr, indent=2))
        return "\n".join(parts)

    def _build_retry_prompt(
        self,
        spec: GenerationSpec,
        last_source: str,
        result: ValidationResult,
        attempt: int,
    ) -> str:
        base = self._build_named_user_prompt(spec) if spec.method_name else self._build_user_prompt(spec)
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

    def generate(self, spec: GenerationSpec) -> CacheEntry:
        total_attempts = self.max_retries + 1

        decision = spec.decision if spec.decision is not None else Decision.GENERATE
        with generation_progress(self.verbose) as progress:
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

            if spec.method_name:
                prompt = self._build_named_user_prompt(spec)
                system_prompt = SYSTEM_PROMPT
                tool_refs = []
            else:
                prompt = self._build_user_prompt(spec)
                system_prompt = inject_tools_into_system_prompt(SYSTEM_PROMPT, prompt)
                tool_refs = parse_tool_refs(prompt)
            if tool_refs and self.verbose:
                tool_names = ", ".join(sorted({name for name, _ in tool_refs}))
                progress.update(f"Tools detected: {tool_names}. Calling LLM...")
            last_source = ""
            last_result: Optional[ValidationResult] = None

            for attempt in range(total_attempts):
                progress.log_step(f"Generating (attempt {attempt + 1}/{total_attempts})")
                if last_result and (last_result.error_message or ""):
                    print_pipeline_log(spec.call_site, "generate", f"Retry {attempt + 1}/{total_attempts}: fixing validation error")
                    progress.set_stage("generate")
                    progress.update(f"Retrying (attempt {attempt + 1}/{total_attempts}): fixing validation error...")
                elif not tool_refs:
                    print_pipeline_log(spec.call_site, "generate", f"Calling LLM (attempt {attempt + 1}/{total_attempts})")
                    progress.set_stage("generate")
                    progress.update(f"Calling LLM (attempt {attempt + 1}/{total_attempts})...")
                else:
                    print_pipeline_log(spec.call_site, "generate", f"Calling LLM with tools (attempt {attempt + 1}/{total_attempts})")
                    progress.set_stage("generate")
                    progress.update(f"Calling LLM with tools (attempt {attempt + 1}/{total_attempts})...")

                on_chunk = None
                if self.stream and self.verbose:
                    console = get_console()
                    on_chunk = lambda chunk, c=console: c.print(chunk, end="")
                raw = self.generator.generate(
                    system_prompt,
                    prompt,
                    stream=self.stream,
                    on_chunk=on_chunk,
                )
                if self.stream and self.verbose:
                    get_console().print()

                progress.update("Parsing generated code...")
                source = _extract_function_source(raw)
                print_pipeline_log(spec.call_site, "validate", "Validating (AST, type, execution)")
                progress.set_stage("validate")
                progress.log_step("Validating (AST, type, execution)")
                progress.update("Validating (AST, type, execution)...")
                result = validate(
                    source,
                    expected_type=spec.expected_type,
                    sample_input=spec.sample_input,
                    enable_execution=self.enable_execution_test,
                    usage_hint=getattr(spec, "usage_hint", ""),
                    spec=spec,
                )

                if result.passed:
                    print_pipeline_log(spec.call_site, "validate", "Valid (AST, type, execution)")
                    progress.log_step("Valid")
                    progress.record_success(
                        attempt + 1,
                        call_site=spec.call_site,
                    )
                    fn = _compile_source(source)
                    return CacheEntry(
                        generated_source=source,
                        compiled_fn=fn,
                        expected_type=spec.expected_type,
                    )

                for tool in self.tools:
                    try:
                        tool(spec, source, result)
                    except Exception:
                        pass

                last_source = source
                last_result = result
                prompt = self._build_retry_prompt(spec, source, result, attempt)
                system_prompt = SYSTEM_PROMPT if spec.method_name else inject_tools_into_system_prompt(SYSTEM_PROMPT, prompt)

            if self.confirm_on_failure and last_result is not None:
                config = get_config()
                error_summary = last_result.error_message or "Unknown error"
                if confirm(
                    f"Generation failed after {total_attempts} attempts. Last error: {error_summary}\nRetry with one more attempt?",
                    default_no=True,
                    confirm_callback=config.confirm_callback,
                ):
                    progress.log_step("Retry (user confirmed)")
                    progress.update("Retry (user confirmed)...")
                    raw = self.generator.generate(
                        system_prompt,
                        prompt,
                        stream=self.stream,
                        on_chunk=(
                            (lambda c=get_console(): lambda chunk: c.print(chunk, end=""))()
                            if self.stream and self.verbose
                            else None
                        ),
                    )
                    if self.stream and self.verbose:
                        get_console().print()
                    source = _extract_function_source(raw)
                    result = validate(
                        source,
                        expected_type=spec.expected_type,
                        sample_input=spec.sample_input,
                        enable_execution=self.enable_execution_test,
                        usage_hint=getattr(spec, "usage_hint", ""),
                        spec=spec,
                    )
                    if result.passed:
                        progress.record_success(
                            total_attempts + 1,
                            call_site=spec.call_site,
                        )
                        fn = _compile_source(source)
                        return CacheEntry(
                            generated_source=source,
                            compiled_fn=fn,
                            expected_type=spec.expected_type,
                        )

            progress.record_failure(
                last_result.error_message if last_result else "Unknown error",
                validation_result=last_result,
                source=last_source if last_source else None,
                call_site=spec.call_site,
            )
        raise SemiGenerationError(
            f"Failed to generate valid function after {self.max_retries + 1} attempts. "
            + (last_result.error_message if last_result else "Unknown error.")
        )
