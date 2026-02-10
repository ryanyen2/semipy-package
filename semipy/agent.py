"""Agentic generate-validate-retry loop for semi() function generation."""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from semipy.cache import SemiCache, build_template_hash, _compile_source
from semipy.config import get_config
from semipy.generator import SemiGenerator, SYSTEM_PROMPT
from semipy.types import (
    CacheEntry,
    GenerationSpec,
    GenerationStrategy,
    SemiGenerationError,
    ValidationResult,
)
from semipy.validator import validate, _extract_function_source


class SemiAgent:
    """Generates a Python function from a semantic prompt, with validation and retries."""

    def __init__(
        self,
        generator: Optional[SemiGenerator] = None,
        cache: Optional[SemiCache] = None,
        max_retries: Optional[int] = None,
        enable_execution_test: Optional[bool] = None,
    ):
        config = get_config()
        self.generator = generator or SemiGenerator()
        self.cache = cache or SemiCache(config.cache_dir)
        self.max_retries = max_retries if max_retries is not None else config.max_retries
        self.enable_execution_test = (
            enable_execution_test if enable_execution_test is not None else config.enable_execution_test
        )

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
        if strategy == GenerationStrategy.REUSE and spec.template:
            template_hash = build_template_hash(
                spec.template.template_parts,
                spec.constant_values or {},
            )
            existing = self.cache.get(spec.call_site.site_id, template_hash)
            if existing and existing.compiled_fn:
                return existing

        prompt = self._build_user_prompt(spec)
        last_source = ""
        last_result: Optional[ValidationResult] = None

        for attempt in range(self.max_retries + 1):
            raw = self.generator.generate(SYSTEM_PROMPT, prompt)
            source = _extract_function_source(raw)

            result = validate(
                source,
                expected_type=spec.expected_type,
                sample_input=spec.sample_input,
                enable_execution=self.enable_execution_test,
            )

            if result.passed:
                fn = _compile_source(source)
                if spec.template:
                    template_hash = build_template_hash(
                        spec.template.template_parts,
                        spec.constant_values or {},
                    )
                else:
                    import hashlib
                    template_hash = hashlib.sha256(spec.prompt.encode()).hexdigest()[:16]
                entry = CacheEntry(
                    site_id=spec.call_site.site_id,
                    template_hash=template_hash,
                    generated_source=source,
                    compiled_fn=fn,
                    expected_type=spec.expected_type,
                )
                self.cache.put(entry)
                return entry

            last_source = source
            last_result = result
            prompt = self._build_retry_prompt(spec, source, result, attempt)

        raise SemiGenerationError(
            f"Failed to generate valid function after {self.max_retries + 1} attempts. "
            + (last_result.error_message if last_result else "Unknown error.")
        )
