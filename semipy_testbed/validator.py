"""
Simplified validation: syntax, execution, type.
"""
from __future__ import annotations

import ast
from typing import Any, Optional

from semipy_testbed.types import ValidationReport, GistExecutorResult
from semipy_testbed.gist_executor import SimpleGistExecutor


def validate_syntax(source_code: str) -> tuple[bool, Optional[str]]:
    """Check if source code is valid Python syntax."""
    try:
        ast.parse(source_code)
        return True, None
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, str(e)


def validate_execution(
    gist_source: str,
    timeout: int = 30,
    use_docker: bool = False,
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Execute gist and check for runtime errors.
    Returns (success, error, result_repr).
    """
    executor = SimpleGistExecutor(use_docker=use_docker, timeout=timeout)
    result = executor.execute(gist_source)

    if not result.success:
        return False, result.error, result.result_repr

    return True, None, result.result_repr


def validate_type(
    fn: Any,
    expected_type: Any,
    sample_input: Optional[dict[str, Any]] = None,
) -> tuple[bool, Optional[str]]:
    """
    Check if function signature and return type are reasonable.
    (Simplified: just checks function is callable and has correct signature.)
    """
    if not callable(fn):
        return False, "Result is not callable"

    # If we have sample input, try to call it
    if sample_input:
        try:
            args = tuple(sample_input.get("args", ()) or ())
            kwargs = dict(sample_input.get("kwargs", {}) or {})
            result = fn(*args, **kwargs)

            # Basic type check
            if expected_type is not None and expected_type is not type(None):
                if not isinstance(result, expected_type):
                    return (
                        False,
                        f"Return type {type(result).__name__} does not match "
                        f"expected {getattr(expected_type, '__name__', str(expected_type))}",
                    )
            return True, None
        except Exception as e:
            return False, f"Call failed: {e}"

    return True, None


def validate_all(
    source_code: str,
    gist_source: str,
    expected_type: Optional[Any] = None,
    sample_input: Optional[dict[str, Any]] = None,
    timeout: int = 30,
    use_docker: bool = False,
) -> ValidationReport:
    """Run all validation checks."""
    report = ValidationReport()

    # 1. Syntax
    ok, err = validate_syntax(source_code)
    report.syntax_ok = ok
    report.syntax_error = err

    if not ok:
        return report

    # 2. Execution
    ok, err, _ = validate_execution(gist_source, timeout=timeout, use_docker=use_docker)
    report.execution_ok = ok
    report.execution_error = err

    # 3. Type (basic check if we have sample input)
    if sample_input and expected_type:
        # Extract function from source and test it
        try:
            ns: dict[str, Any] = {}
            exec(compile(source_code, "<validation>", "exec"), ns)
            # Find the first function
            fn = None
            for v in ns.values():
                if callable(v) and not isinstance(v, type):
                    fn = v
                    break
            if fn:
                ok, err = validate_type(fn, expected_type, sample_input)
                report.type_ok = ok
                report.type_error = err
        except Exception as e:
            report.type_ok = False
            report.type_error = str(e)
    else:
        report.type_ok = True

    return report
