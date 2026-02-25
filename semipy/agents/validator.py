"""Context-aware validation of generated semi() functions.

When spec provides caller context (context, caller_locals, source_file_imports),
we build a minimal partial program: substitute the generated function for semi(),
execute the enclosing statement from the user's function, and check for errors.
If execution raises, the traceback is returned as the validation error for the
repair loop. Otherwise we fall back to basic execution with sample_input.
"""
from __future__ import annotations

import ast
import inspect
import traceback
from typing import Any, Callable, Optional

from semipy.types import GenerationSpec, PromptTemplate, ValidationResult

_UNKNOWN_RETURN_NONE_MSG = (
    "Return type is unknown (expected_type is None) and the function returned None. "
    "Consider adding a type hint so validation can verify the result."
)


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


def _extract_enclosing_statement(
    source_code: str,
    semi_call_lineno: int,
    first_lineno: int,
) -> Optional[str]:
    """Return the source of the top-level statement that contains the semi() call line, or None."""
    if not source_code.strip() or semi_call_lineno < first_lineno:
        return None
    rel = semi_call_lineno - first_lineno + 1  # 1-based line within function source
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None
    if not tree.body or not isinstance(tree.body[0], ast.FunctionDef):
        return None
    func = tree.body[0]
    for stmt in func.body:
        start = stmt.lineno
        end = getattr(stmt, "end_lineno", stmt.lineno)
        if start <= rel <= end:
            seg = ast.get_source_segment(source_code, stmt)
            return seg.strip() if seg else None
    return None


def _build_mock_semi(
    generated_fn: Callable[..., Any],
    template: Optional[PromptTemplate],
) -> Any:
    """Return a mock 'semi' usable in exec namespace: __call__ for inline, __getattr__ for semi.name()."""

    class _MockSemi:
        def __init__(self, fn: Callable[..., Any], tpl: Optional[PromptTemplate]) -> None:
            self._fn = fn
            self._template = tpl

        def __call__(self, prompt: str, *, expected_type: Any = None, **kw: Any) -> Any:
            frame = inspect.currentframe()
            if frame is not None and frame.f_back is not None and self._template is not None:
                exprs = getattr(self._template, "variable_expressions", None) or []
                values = []
                globals_dict = frame.f_back.f_globals
                locals_dict = frame.f_back.f_locals
                for expr in exprs:
                    try:
                        values.append(eval(expr, globals_dict, locals_dict))
                    except Exception:
                        values.append(None)
                return self._fn(*values)
            return self._fn(prompt)

        def __getattr__(self, name: str) -> Callable[..., Any]:
            def _named(*args: Any, **kwargs: Any) -> Any:
                return self._fn(*args, **kwargs)

            return _named

    return _MockSemi(generated_fn, template)


def _validate_in_context(source: str, spec: GenerationSpec) -> ValidationResult:
    """Compile generated function, build namespace with imports + caller_locals + mock semi, exec enclosing statement."""
    from semipy.agents.compiler import _compile_source

    try:
        fn = _compile_source(source)
    except Exception as e:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message=f"Compile failed: {e}",
        )
    if fn is None:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message="No callable in compiled source",
        )

    context = spec.context
    if context is None or spec.caller_locals is None:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message="Missing context or caller_locals for in-context validation",
        )

    imports = spec.source_file_imports or []
    namespace: dict[str, Any] = {}
    for imp in imports:
        try:
            exec(imp, namespace)
        except Exception:
            pass
    namespace.update(spec.caller_locals)
    mock = _build_mock_semi(fn, spec.template)
    namespace["semi"] = mock

    statement = _extract_enclosing_statement(
        context.source_code,
        spec.call_site.lineno,
        getattr(context, "first_lineno", 1),
    )
    if not statement:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message="Could not extract enclosing statement for in-context execution",
        )

    try:
        exec(compile(statement, "<context_validation>", "exec"), namespace, namespace)
    except Exception:
        tb = traceback.format_exc()
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message=tb,
        )

    return ValidationResult(
        passed=True,
        ast_valid=True,
        type_correct=True,
        execution_ok=True,
        error_message="",
    )


def _validate_result_type_pydantic(result: Any, expected_type: type) -> Optional[str]:
    """
    Validate result against expected_type using pydantic TypeAdapter when available.
    Returns an error message if validation fails, None if it passes or pydantic is unavailable.
    """
    if expected_type is type(None):
        return None
    try:
        from pydantic import TypeAdapter
        from pydantic import ValidationError as PydanticValidationError
    except ImportError:
        return None
    try:
        adapter = TypeAdapter(expected_type)
        adapter.validate_python(result)
        return None
    except PydanticValidationError as e:
        return str(e)
    except Exception:
        return None


def _validate_basic_execution(
    expected_type: type,
    sample_input: Optional[dict[str, Any]],
    fn: Any,
) -> ValidationResult:
    """Run generated function with sample_input and check return type. Uses pydantic when available for strict type validation."""
    if sample_input is None:
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )
    try:
        args = sample_input.get("args", ())
        kwargs = sample_input.get("kwargs", {})
        result = fn(*args, **kwargs)
        if expected_type is type(None) and result is None:
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=True,
                execution_ok=True,
                error_message=_UNKNOWN_RETURN_NONE_MSG,
            )
        if expected_type is not type(None) and not isinstance(result, expected_type):
            pydantic_err = _validate_result_type_pydantic(result, expected_type)
            msg = (
                f"Return value did not match expected type {expected_type.__name__}: {pydantic_err}"
                if pydantic_err
                else f"Returned {type(result).__name__}, expected {expected_type.__name__}"
            )
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=False,
                execution_ok=True,
                error_message=msg,
            )
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )
    except Exception:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message=traceback.format_exc(),
        )


def validate(
    raw_source: str,
    expected_type: type,
    sample_input: Optional[dict[str, Any]] = None,
    enable_execution: bool = True,
    usage_hint: str = "",
    spec: Optional[GenerationSpec] = None,
) -> ValidationResult:
    """
    Stage 1: AST parse and ensure one function def.
    Stage 2: If spec has context (context, caller_locals): context-aware execution of enclosing statement.
    Otherwise: basic execution with sample_input and return type check.
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
    type_ok = True

    if not enable_execution:
        return ValidationResult(
            passed=ast_valid and type_ok,
            ast_valid=ast_valid,
            type_correct=type_ok,
            execution_ok=True,
            error_message="",
        )

    use_context = (
        spec is not None
        and spec.context is not None
        and spec.caller_locals is not None
    )
    if use_context:
        statement = _extract_enclosing_statement(
            spec.context.source_code,
            spec.call_site.lineno,
            getattr(spec.context, "first_lineno", 1),
        )
        if statement is not None and not statement.strip().startswith("return "):
            return _validate_in_context(source, spec)

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
    except Exception as e:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message=str(e),
        )

    effective_type = spec.expected_type if spec is not None else expected_type
    effective_sample = spec.sample_input if spec is not None else sample_input
    return _validate_basic_execution(effective_type, effective_sample, fn)


def validate_with_gist(
    source: str,
    spec: GenerationSpec,
    gist_builder: Any,
    executor: Any,
) -> ValidationResult:
    """
    Validate generated function by building a gist and running it in the sandbox.
    Returns ValidationResult with gist_executed, gist_stdout, gist_stderr set.
    Falls back to standard validate() if gist building fails.
    """
    source = _extract_function_source(source)
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
    gist = gist_builder.build(source)
    if gist is None:
        return validate(source, spec.expected_type, spec.sample_input, True, getattr(spec, "usage_hint", ""), spec)
    result = executor.execute_sync(gist.source)
    from semipy.agents.compiler import _compile_source
    try:
        fn = _compile_source(source)
    except Exception as e:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=False,
            execution_ok=False,
            error_message=str(e),
            gist_executed=result.success,
            gist_stdout=result.stdout,
            gist_stderr=result.stderr,
        )
    exec_result = _validate_basic_execution(spec.expected_type, spec.sample_input, fn)
    return ValidationResult(
        passed=exec_result.passed,
        ast_valid=exec_result.ast_valid,
        type_correct=exec_result.type_correct,
        execution_ok=exec_result.execution_ok,
        error_message=exec_result.error_message,
        gist_executed=result.success,
        gist_stdout=result.stdout,
        gist_stderr=result.stderr,
    )
