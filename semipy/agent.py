"""Agentic generate-validate-retry loop for semi() function generation."""
from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

from semipy.cache import _compile_source
from semipy.config import get_config
from semipy.console_io import (
    confirm,
    generation_progress,
    get_console,
    source_preview,
    strategy_description,
    validation_error_panel,
)
from semipy.generator import SemiGenerator, SYSTEM_PROMPT
from semipy.change_guidance import format_change_summary_for_prompt
from semipy.types import (
    CacheEntry,
    GenerationSpec,
    GenerationStrategy,
    SemiGenerationError,
    SemiTool,
    ValidationResult,
)
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

    def _choose_strategy(self, spec: GenerationSpec) -> GenerationStrategy:
        return GenerationStrategy.FRESH

    def _build_user_prompt(self, spec: GenerationSpec) -> str:
        parts = [
            "Implement a single Python function that satisfies this request:",
            "",
            spec.prompt,
            "",
            "Constraints:",
            f"- Return type must be: {spec.expected_type.__name__ if spec.expected_type is not type(None) else 'any'}",
        ]
        if spec.change_summary is not None:
            parts.append("")
            parts.append("Change context (use to decide refactor vs full rewrite):")
            parts.append(format_change_summary_for_prompt(spec.change_summary))
        if spec.existing_implementation_source:
            parts.append("")
            parts.append("Existing implementation (refactor or replace as needed):")
            parts.append("```python")
            parts.append(spec.existing_implementation_source.strip())
            parts.append("```")
        if spec.template and spec.template.variable_names:
            n = len(spec.template.variable_names)
            parts.append(
                f"- The function will be called with exactly {n} positional arguments (in order). "
                "The first argument is the value that changes per invocation; the rest are fixed context for this call."
            )
        if spec.sample_input:
            parts.append("- Sample input for testing:")
            parts.append(json.dumps(spec.sample_input, default=repr, indent=2))
        if spec.constant_values:
            parts.append("- Constant context (use as parameters after the first, or bake into the function):")
            parts.append(json.dumps(spec.constant_values, default=repr, indent=2))
        return "\n".join(parts)

    def _build_retry_prompt(
        self,
        spec: GenerationSpec,
        last_source: str,
        result: ValidationResult,
        attempt: int,
    ) -> str:
        return (
            self._build_user_prompt(spec)
            + "\n\nPrevious attempt failed validation:\n"
            + result.error_message
            + "\n\nFix the function and output a corrected version in a ```python block."
        )

    def generate(self, spec: GenerationSpec) -> CacheEntry:
        strategy = self._choose_strategy(spec)
        total_attempts = self.max_retries + 1

        with generation_progress(self.verbose) as progress:
            progress.log_step("Generate")
            progress.log_step(f"Strategy: {strategy_description(strategy)}")
            progress.update(f"Cache miss. {strategy_description(strategy)}")
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

            prompt = self._build_user_prompt(spec)
            last_source = ""
            last_result: Optional[ValidationResult] = None

            for attempt in range(total_attempts):
                progress.log_step(f"Generating (attempt {attempt + 1}/{total_attempts})")
                progress.update(
                    f"Generating (attempt {attempt + 1}/{total_attempts})"
                    + (f": {(last_result.error_message or '')[:50]!r}..." if last_result and (last_result.error_message or '') else "")
                )

                on_chunk = None
                if self.stream and self.verbose:
                    console = get_console()
                    on_chunk = lambda chunk, c=console: c.print(chunk, end="")
                raw = self.generator.generate(
                    SYSTEM_PROMPT,
                    prompt,
                    stream=self.stream,
                    on_chunk=on_chunk,
                )
                if self.stream and self.verbose:
                    get_console().print()

                source = _extract_function_source(raw)
                progress.log_step("Validating (AST, type, execution)")
                progress.update("Validating (AST, type, execution)...")
                result = validate(
                    source,
                    expected_type=spec.expected_type,
                    sample_input=spec.sample_input,
                    enable_execution=self.enable_execution_test,
                )

                if result.passed:
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
                        SYSTEM_PROMPT,
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
