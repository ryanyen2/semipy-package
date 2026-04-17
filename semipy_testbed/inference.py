"""
Main inference pipeline: parse spec → generate → validate → return function.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Optional, Callable
from dataclasses import dataclass

from semipy_testbed.config import get_config
from semipy_testbed.types import SimpleInferenceResult, ValidationReport
from semipy_testbed.gist_builder import SimpleGistBuilder
from semipy_testbed.gist_executor import SimpleGistExecutor
from semipy_testbed.validator import validate_all, validate_syntax


@dataclass
class InferenceRequest:
    """Request for semiformal inference."""

    user_spec: str  # Natural language spec or semi() template
    free_variables: dict[str, Any]  # Runtime values
    sample_input: Optional[dict[str, Any]] = None  # Test data
    expected_type: Optional[type] = None  # Return type hint
    free_variable_names: Optional[list[str]] = None  # Names in order
    user_source_code: Optional[str] = None  # Full source for context


def _build_generation_prompt(request: InferenceRequest) -> str:
    """Build LLM prompt for code generation."""
    prompt_parts = []

    prompt_parts.append("You are an expert Python code generator.")
    prompt_parts.append("")
    prompt_parts.append("## Task")
    prompt_parts.append(f"Generate a Python function based on this specification:")
    prompt_parts.append("")
    prompt_parts.append(f"```")
    prompt_parts.append(request.user_spec)
    prompt_parts.append("```")
    prompt_parts.append("")

    if request.free_variables:
        prompt_parts.append("## Input Variables")
        for name, value in request.free_variables.items():
            prompt_parts.append(f"- {name}: {type(value).__name__} = {repr(value)[:100]}")
        prompt_parts.append("")

    if request.expected_type:
        type_name = getattr(request.expected_type, "__name__", str(request.expected_type))
        prompt_parts.append(f"## Expected Return Type")
        prompt_parts.append(f"Return type: {type_name}")
        prompt_parts.append("")

    if request.user_source_code:
        prompt_parts.append("## User Source Context")
        prompt_parts.append("(Use this to infer expected data structures and patterns)")
        prompt_parts.append("```python")
        # Limit context length
        lines = request.user_source_code.split("\n")[:50]
        prompt_parts.append("\n".join(lines))
        prompt_parts.append("```")
        prompt_parts.append("")

    prompt_parts.append("## Requirements")
    prompt_parts.append("1. Return ONLY valid Python code (no markdown, no explanation)")
    prompt_parts.append("2. Include all necessary imports at the top")
    prompt_parts.append("3. Function should handle edge cases gracefully")
    prompt_parts.append("4. The function will be executed in isolation (subprocess/docker)")
    prompt_parts.append("")
    prompt_parts.append("Generate the function now:")

    return "\n".join(prompt_parts)


def _call_openrouter(prompt: str, config: Any) -> tuple[bool, str, Optional[str]]:
    """
    Call OpenRouter API for code generation.
    Returns (success, generated_code, error).
    """
    try:
        from openrouter import OpenRouter
    except ImportError:
        return False, "", "openrouter library not installed (pip install openrouter)"

    if not config.openrouter_api_key:
        return False, "", "OPENROUTER_API_KEY not set"

    try:
        client = OpenRouter(api_key=config.openrouter_api_key)

        response = client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

        generated_code = response.choices[0].message.content
        if not generated_code:
            return False, "", "Empty response from model"

        # Clean up markdown code blocks if present
        if "```python" in generated_code:
            parts = generated_code.split("```python")
            if len(parts) > 1:
                code_part = parts[1].split("```")[0]
                generated_code = code_part.strip()
        elif "```" in generated_code:
            parts = generated_code.split("```")
            if len(parts) > 1:
                generated_code = parts[1].strip()

        return True, generated_code, None

    except Exception as e:
        return False, "", f"OpenRouter API error: {e}"


def infer_semiformal(
    user_spec: str,
    free_variables: Optional[dict[str, Any]] = None,
    sample_input: Optional[dict[str, Any]] = None,
    expected_type: Optional[type] = None,
    free_variable_names: Optional[list[str]] = None,
    user_source_code: Optional[str] = None,
    use_docker: bool = False,
    verbose: bool = False,
) -> SimpleInferenceResult:
    """
    One-shot semiformal inference: parse → generate → validate → return function.

    Args:
        user_spec: Natural language specification or semi() template.
        free_variables: Dict of runtime variable values.
        sample_input: Dict with 'args' and 'kwargs' for test invocation.
        expected_type: Expected return type.
        free_variable_names: Ordered list of free variable names.
        user_source_code: Full user source for context (helps LLM infer intent).
        use_docker: Execute gist in Docker container instead of subprocess.
        verbose: Print debug info.

    Returns:
        SimpleInferenceResult with compiled function or error details.
    """
    config = get_config()

    if verbose:
        print(f"[TESTBED] Inference starting...")
        print(f"[TESTBED] Spec: {user_spec[:50]}...")

    free_variables = free_variables or {}
    free_variable_names = free_variable_names or list(free_variables.keys())

    # Build request
    request = InferenceRequest(
        user_spec=user_spec,
        free_variables=free_variables,
        sample_input=sample_input,
        expected_type=expected_type,
        free_variable_names=free_variable_names,
        user_source_code=user_source_code,
    )

    # Step 1: Generate code via LLM
    if verbose:
        print(f"[TESTBED] Building generation prompt...")

    prompt = _build_generation_prompt(request)
    success, generated_code, error = _call_openrouter(prompt, config)

    if not success:
        return SimpleInferenceResult(
            success=False,
            error=error,
            reasoning=f"Generation failed: {error}",
        )

    if verbose:
        print(f"[TESTBED] Generated code ({len(generated_code)} chars)")

    # Step 2: Validate syntax
    syntax_ok, syntax_err = validate_syntax(generated_code)
    if not syntax_ok:
        return SimpleInferenceResult(
            success=False,
            source_code=generated_code,
            error=f"Generated code has syntax error: {syntax_err}",
        )

    if verbose:
        print(f"[TESTBED] Syntax OK")

    # Step 3: Build gist
    if verbose:
        print(f"[TESTBED] Building gist...")

    gist_builder = SimpleGistBuilder(
        sample_input=sample_input,
        free_variables=free_variable_names,
        user_source_path=None,
    )

    gist = gist_builder.build(generated_code)
    if not gist:
        return SimpleInferenceResult(
            success=False,
            source_code=generated_code,
            error=f"Could not build gist: {gist_builder.last_build_error}",
        )

    if verbose:
        print(f"[TESTBED] Gist built, function={gist.fn_name}")

    # Step 4: Validate execution
    if verbose:
        print(f"[TESTBED] Validating execution...")

    validation_report = validate_all(
        source_code=generated_code,
        gist_source=gist.source,
        expected_type=expected_type,
        sample_input=sample_input,
        timeout=config.timeout,
        use_docker=use_docker,
    )

    if not validation_report.passed:
        if verbose:
            print(f"[TESTBED] Validation failed: {validation_report.error_message}")

        return SimpleInferenceResult(
            success=False,
            source_code=generated_code,
            gist_source=gist.source,
            error=validation_report.error_message,
        )

    if verbose:
        print(f"[TESTBED] Validation passed!")

    # Step 5: Compile and return function
    try:
        ns: dict[str, Any] = {}
        exec(compile(generated_code, "<inference>", "exec"), ns)

        compiled_fn = None
        for v in ns.values():
            if callable(v) and not isinstance(v, type):
                compiled_fn = v
                break

        if not compiled_fn:
            return SimpleInferenceResult(
                success=False,
                source_code=generated_code,
                gist_source=gist.source,
                error="Could not find callable in generated code",
            )

        if verbose:
            print(f"[TESTBED] Inference complete! Function compiled: {compiled_fn.__name__}")

        return SimpleInferenceResult(
            success=True,
            compiled_function=compiled_fn,
            source_code=generated_code,
            gist_source=gist.source,
        )

    except Exception as e:
        return SimpleInferenceResult(
            success=False,
            source_code=generated_code,
            gist_source=gist.source,
            error=f"Compilation error: {e}",
        )
