from __future__ import annotations

import ast
import inspect
import traceback
from typing import Any, Optional

from semipy.types import GenerationSpec, SlotCategory, ValidationResult


def _extract_function_source(raw: str) -> str:
    """Extract Python code from markdown code block if present."""
    raw = raw.strip()
    if "```python" in raw:
        start = raw.index("```python") + len("```python")
        end = raw.find("```", start)
        if end != -1:
            return raw[start:end].strip()
    if "```" in raw:
        start = raw.index("```") + 3
        end = raw.find("```", start)
        if end != -1:
            return raw[start:end].strip()
    return raw


def _validate_formal_constraints(generated_source: str, formal_constraints: list[str]) -> Optional[str]:
    if not formal_constraints:
        return None
    for line in formal_constraints:
        stripped = (line or "").strip()
        if not stripped:
            continue
        if stripped not in generated_source:
            return f"Missing formal constraint line in generated source: {stripped!r}"
    return None


def _validate_callable_shape(result: Any) -> bool:
    return callable(result)


def _validate_basic_execution(
    *,
    fn: Any,
    expected_type: Any,
    sample_input: Optional[dict[str, Any]],
    slot_category: SlotCategory | None,
    output_names: list[str],
) -> ValidationResult:
    # We intentionally do not require non-None results for unknown types.
    if sample_input is None:
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )
    args = sample_input.get("args", ()) or ()
    kwargs = sample_input.get("kwargs", {}) or {}

    try:
        # Fast, deterministic signature check so failures don't require executing the function body.
        try:
            sig = inspect.signature(fn)
            sig.bind(*args, **kwargs)
        except TypeError as e:
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=True,
                execution_ok=False,
                error_message=f"Signature mismatch: {e}",
            )

        result = fn(*args, **kwargs)
    except Exception:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message=traceback.format_exc(),
        )

    # Category shape checks.
    if slot_category == SlotCategory.STATEMENT_BLOCK:
        if output_names:
            if not isinstance(result, dict):
                return ValidationResult(
                    passed=False,
                    ast_valid=True,
                    type_correct=False,
                    execution_ok=True,
                    error_message=f"STATEMENT_BLOCK must return dict; got {type(result).__name__}",
                )
            if set(result.keys()) != set(output_names):
                return ValidationResult(
                    passed=False,
                    ast_valid=True,
                    type_correct=False,
                    execution_ok=True,
                    error_message=f"STATEMENT_BLOCK dict keys mismatch. expected={output_names} got={list(result.keys())}",
                )
        else:
            # Side-effect blocks are allowed to return None.
            if result is not None:
                return ValidationResult(
                    passed=False,
                    ast_valid=True,
                    type_correct=True,
                    execution_ok=True,
                    error_message=f"STATEMENT_BLOCK with no output_names must return None; got {type(result).__name__}",
                )

    # Return type checks.
    if expected_type is type(None):
        # Unknown type: accept any.
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )

    if expected_type is callable:
        if not _validate_callable_shape(result):
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=False,
                execution_ok=True,
                error_message=f"Expected a callable result; got {type(result).__name__}",
            )
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )

    # Domain classes and plain Python types.
    if isinstance(expected_type, type):
        if not isinstance(result, expected_type):
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=False,
                execution_ok=True,
                error_message=f"Returned {type(result).__name__}, expected {expected_type.__name__}",
            )
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )

    # Fallback for non-type expected_type: best-effort acceptance.
    return ValidationResult(
        passed=True,
        ast_valid=True,
        type_correct=True,
        execution_ok=True,
        error_message="",
    )


def validate(
    raw_source: str,
    expected_type: Any,
    sample_input: Optional[dict[str, Any]] = None,
    enable_execution: bool = True,
    usage_hint: str = "",
    spec: Optional[GenerationSpec] = None,
) -> ValidationResult:
    """
    Validate generated slot implementation:
    - AST parse + ensure one function def
    - execute with sample_input (if enable_execution)
    - enforce STATEMENT_BLOCK return dict keys
    - enforce formal_constraints presence (verbatim substring match)
    """
    del usage_hint  # usage_hint is legacy; constraints are handled via SlotSpec.

    source = _extract_function_source(raw_source)
    if not source.strip():
        return ValidationResult(
            passed=False,
            ast_valid=False,
            type_correct=False,
            execution_ok=False,
            error_message="No Python code found in response",
        )

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ValidationResult(
            passed=False,
            ast_valid=False,
            type_correct=False,
            execution_ok=False,
            error_message=f"Syntax error: {e}",
        )

    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    if not funcs:
        return ValidationResult(
            passed=False,
            ast_valid=False,
            type_correct=False,
            execution_ok=False,
            error_message="No function definition found",
        )

    if not enable_execution:
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )

    # Formal constraint check first; it's purely textual.
    formal_constraints = (spec.slot_spec.formal_constraints if spec and spec.slot_spec else []) if spec is not None else []
    constraint_err = _validate_formal_constraints(source, formal_constraints)
    if constraint_err is not None:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message=constraint_err,
        )

    try:
        ns: dict[str, Any] = {}
        exec(compile(source, "<generated>", "exec"), ns)
        fns = [v for v in ns.values() if callable(v) and not isinstance(v, type)]
        if not fns:
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=True,
                execution_ok=False,
                error_message="No callable in compiled source",
            )
        fn = fns[0]
    except Exception:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message=traceback.format_exc(),
        )

    slot_category = spec.slot_spec.expected_category if spec and spec.slot_spec else None
    output_names = spec.slot_spec.output_names if spec and spec.slot_spec else []

    effective_type = spec.expected_type if spec is not None else expected_type
    effective_sample = spec.sample_input if spec is not None and spec.sample_input is not None else sample_input

    return _validate_basic_execution(
        fn=fn,
        expected_type=effective_type,
        sample_input=effective_sample,
        slot_category=slot_category,
        output_names=output_names,
    )

