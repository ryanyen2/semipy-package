"""Three-stage validation of generated semi() functions."""
from __future__ import annotations

import ast
from typing import Any, Optional

from semipy.types import ValidationResult


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


def _get_return_type_from_ast(tree: ast.AST) -> type:
    """Infer return type from the single function def in the source."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.returns:
            if isinstance(node.returns, ast.Name):
                name = node.returns.id
                if name == "bool":
                    return bool
                if name == "str":
                    return str
                if name == "int":
                    return int
                if name == "float":
                    return float
            return type(None)
    return type(None)


def validate(
    raw_source: str,
    expected_type: type,
    sample_input: Optional[dict[str, Any]] = None,
    enable_execution: bool = True,
) -> ValidationResult:
    """
    Stage 1: AST - parse and ensure one function def.
    Stage 2: Type - return type matches expected.
    Stage 3: Execution - run with sample_input, check no exception and return type.
    """
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

    ast_valid = True
    inferred = _get_return_type_from_ast(tree)
    type_ok = (
        expected_type is type(None)
        or inferred is expected_type
        or inferred is type(None)
    )
    if not type_ok and expected_type is not type(None):
        try:
            type_ok = issubclass(inferred, expected_type)
        except TypeError:
            pass

    execution_ok = True
    exec_error = ""
    if enable_execution and sample_input is not None:
        try:
            ns: dict[str, Any] = {}
            exec(compile(source, "<generated>", "exec"), ns)
            fns = [v for v in ns.values() if callable(v) and not isinstance(v, type)]
            if not fns:
                exec_error = "No callable in compiled source"
                execution_ok = False
            else:
                fn = fns[0]
                args = sample_input.get("args", ())
                kwargs = sample_input.get("kwargs", {})
                result = fn(*args, **kwargs)
                if expected_type is not type(None) and not isinstance(result, expected_type):
                    execution_ok = False
                    exec_error = f"Returned {type(result).__name__}, expected {expected_type.__name__}"
        except Exception as e:
            execution_ok = False
            exec_error = str(e)

    passed = ast_valid and type_ok and execution_ok
    return ValidationResult(
        passed=passed,
        ast_valid=ast_valid,
        type_correct=type_ok,
        execution_ok=execution_ok,
        error_message=exec_error or ("" if passed else "Validation failed"),
    )
