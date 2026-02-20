"""Three-stage validation of generated semi() functions.

Validation runs the generated function in isolation with sample_input. To catch
errors that only appear when the return value is used by the callee, we
optionally run a usage-context check: when usage_hint describes "passed as
argument N to <path>", we try to resolve that callable and call it with the
result. If the callee raises, we use that exception as the validation error
(no library-specific rules). If the callable cannot be resolved safely, we
skip. Runtime errors remain the source of truth and produce SemiCallError
with full context; regeneration can use that feedback on the next run.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Optional

from semipy.types import ValidationResult

# Allowed module prefixes for usage-context execution (e.g. calling plt.x or np.y with result).
_USAGE_CONTEXT_IMPORTS: dict[str, str] = {
    "plt": "matplotlib.pyplot",
    "np": "numpy",
}


def _run_in_usage_context(usage_hint: str, result: Any) -> Optional[str]:
    """If usage_hint is 'passed as argument N to <path>', try calling that with result; return error message or None."""
    if not usage_hint:
        return None
    m = re.search(r"passed as argument \d+ to ([a-zA-Z0-9_.()]+)", usage_hint, re.IGNORECASE)
    if not m:
        return None
    path = m.group(1).strip()
    if not path:
        return None
    parts = path.split(".")
    if not parts:
        return None
    prefix = parts[0]
    if prefix not in _USAGE_CONTEXT_IMPORTS:
        return None
    try:
        mod = __import__(_USAGE_CONTEXT_IMPORTS[prefix], fromlist=[prefix])
    except Exception:
        return None
    ns: dict[str, Any] = {prefix: mod}
    try:
        if len(parts) == 1:
            callable_obj = mod
        else:
            receiver_expr = ".".join(parts[:-1])
            receiver = eval(receiver_expr, ns)
            callable_obj = getattr(receiver, parts[-1])
        callable_obj(result)
        return None
    except Exception as e:
        return f"When calling {path}(result): {type(e).__name__}: {e}"


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


def _is_numeric_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float))
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant):
        return isinstance(node.operand.value, (int, float))
    return False


def _check_anti_patterns(tree: ast.AST) -> Optional[str]:
    """
    AST-based checks for generated code anti-patterns. Returns an error message
    if the code should be rejected (so the LLM can retry with guidance); None if ok.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(node.value, ast.List) and node.value.elts:
                    elts = node.value.elts
                    if len(elts) > 5 and all(
                        isinstance(e, ast.Constant) and isinstance(e.value, str) and len(str(e.value)) < 50
                        for e in elts
                    ):
                        return (
                            f"Assignment to {target.id!r}: avoid large lists of short string literals (keyword lists). "
                            "Use the actual data context (sample rows, value distributions) to implement logic instead."
                        )
                if isinstance(node.value, ast.Dict) and node.value.keys:
                    keys = node.value.keys
                    values = node.value.values
                    if len(keys) >= 4 and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in keys):
                        if all(_is_numeric_literal(v) for v in values):
                            return (
                                f"Assignment to {target.id!r}: avoid hardcoded lookup dicts with many string keys. "
                                "Use the data context to derive or parameterize values instead."
                            )
                if isinstance(node.value, (ast.List, ast.Tuple)) and getattr(node.value, "elts", None):
                    elts = node.value.elts
                    if len(elts) > 8 and all(
                        isinstance(e, (ast.Tuple, ast.List)) and len(getattr(e, "elts", [])) == 2 for e in elts
                    ):
                        return (
                            f"Assignment to {target.id!r}: avoid long operator/pattern tuple chains. "
                            "Implement logic from the prompt and data context instead of pattern tables."
                        )
    return None


def _get_return_type_from_ast(tree: ast.AST) -> type:
    """Infer return type from the single function def in the source."""
    _name_to_type = {
        "bool": bool,
        "str": str,
        "int": int,
        "float": float,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
        "bytes": bytes,
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.returns:
            ret = node.returns
            if isinstance(ret, ast.Name):
                return _name_to_type.get(ret.id, type(None))
            if isinstance(ret, ast.Subscript) and isinstance(ret.value, ast.Name):
                return _name_to_type.get(ret.value.id, type(None))
            if isinstance(ret, ast.Constant) and ret.value is None:
                return type(None)
            return type(None)
    return type(None)


def validate(
    raw_source: str,
    expected_type: type,
    sample_input: Optional[dict[str, Any]] = None,
    enable_execution: bool = True,
    usage_hint: str = "",
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

    anti_msg = _check_anti_patterns(tree)
    if anti_msg is not None:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=False,
            execution_ok=False,
            error_message=anti_msg,
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
                elif expected_type is type(None) and result is None:
                    execution_ok = False
                    exec_error = _UNKNOWN_RETURN_NONE_MSG
                else:
                    usage_msg = _run_in_usage_context(usage_hint, result)
                    if usage_msg:
                        execution_ok = False
                        exec_error = usage_msg
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
