from __future__ import annotations

import ast
import inspect
import traceback
from typing import Any, Optional, get_args, get_origin

from semipy.agents.slot_call import invoke_slot
from semipy.type_adapter import type_adapter_for as _type_adapter_for
from semipy.types import GenerationSpec, SlotCategory, ValidationResult


def _should_use_typeadapter_for_expected_type(expected_type: Any) -> bool:
    """
    When True, validate slot results with pydantic TypeAdapter(expected_type).

    Skip loose containers that would accept any dict/list without checking structure
    (the historical gap that let ``dict[str, Any]`` slots commit garbage payloads).
    """
    if expected_type is None or expected_type is type(None):
        return False
    if expected_type is Any:
        return False
    if expected_type in (dict, list, set, frozenset):
        return False
    origin = get_origin(expected_type)
    if origin in (dict,):
        return False
    return True


def _validate_value_with_typeadapter(
    value: Any,
    expected_type: Any,
    globals_namespace: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    try:
        if globals_namespace is None:
            ta = _type_adapter_for(expected_type)
        else:
            ta = _type_adapter_for(expected_type, globals_namespace=globals_namespace)
        ta.validate_python(value)
    except Exception as e:
        return False, f"TypeAdapter validation failed for {expected_type!r}: {e}"

    # Pydantic coerces dicts to dataclasses during validation, which masks functions
    # that return plain dicts instead of proper class instances.  The committed
    # function still returns dicts at runtime, causing downstream attribute errors.
    # For list[T] / set[T] where T is a user-defined class, enforce isinstance on
    # the first few elements before accepting the result.
    origin = get_origin(expected_type)
    type_args = get_args(expected_type)
    if origin is list and type_args:
        elem_type = type_args[0]
        if isinstance(elem_type, type) and elem_type.__module__ not in ("builtins", "typing"):
            if isinstance(value, list):
                for i, elem in enumerate(value[:5]):
                    if not isinstance(elem, elem_type):
                        return False, (
                            f"list element {i} is {type(elem).__name__!r}, expected {elem_type.__name__!r}. "
                            f"The function must return {elem_type.__name__} instances constructed via "
                            f"{elem_type.__name__}(...), not plain dicts or other surrogate types."
                        )

    return True, ""


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


def _str_identity_passthrough_failure(
    *,
    slot_category: SlotCategory | None,
    expected_type: Any,
    sample_input: dict[str, Any],
    value_for_typecheck: Any,
) -> bool:
    """
    Detect ``return input`` style failure (common when strptime fails but code returns the
    original string). Same structural role as empty-string failure; forces ADAPT on REUSE
    when new rows appear without re-generation.
    """
    if slot_category not in (
        SlotCategory.FUNCTION_BODY,
        SlotCategory.EXPRESSION,
        SlotCategory.EXPRESSION_STANDALONE,
    ):
        return False
    if expected_type is not str or not isinstance(value_for_typecheck, str):
        return False
    args = sample_input.get("args", ()) or ()
    kwargs = sample_input.get("kwargs", {}) or {}
    str_inputs = [x for x in (*args, *kwargs.values()) if isinstance(x, str)]
    if len(str_inputs) != 1:
        return False
    sin = str_inputs[0].strip()
    out = value_for_typecheck.strip()
    if not sin or out != sin:
        return False
    # Avoid false positives on short canonical outputs (e.g. "Mar 2025" is 8 chars).
    return len(sin) >= 9


def _validate_basic_execution(
    *,
    fn: Any,
    expected_type: Any,
    sample_input: Optional[dict[str, Any]],
    slot_category: SlotCategory | None,
    output_names: list[str],
    typeadapter_globals: dict[str, Any] | None = None,
    free_variables: list[str] | None = None,
    usage_hints: list[str] | None = None,
    effectful: bool = False,
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
    args = tuple(sample_input.get("args", ()) or ())
    kwargs = dict(sample_input.get("kwargs", {}) or {})
    fv = free_variables or []
    hints = set(usage_hints or [])

    # Effectful slot: the function emits a reified EffectScript via the injected
    # ``fx`` capability rather than returning a value. Validation here only
    # confirms it runs without error given fx + sample inputs; the effect itself
    # is verified/gated by the effects subsystem (Stage 1+), not by return-type
    # checks. The recorded script is always well-typed by construction.
    if effectful:
        from semipy.effects.inject import make_recorder

        recorder = make_recorder()
        try:
            if fv and len(fv) == len(args) and not kwargs:
                invoke_slot(fn, fv, args, extra_kwargs={"fx": recorder})
            else:
                fn(*args, fx=recorder, **kwargs)
        except TypeError as e:
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=True,
                execution_ok=False,
                error_message=f"Signature mismatch: {e}",
                failure_kind="signature_mismatch",
            )
        except Exception:
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=True,
                execution_ok=False,
                error_message=traceback.format_exc(),
                failure_kind="execution_error",
            )
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )

    try:
        if fv and len(fv) == len(args) and not kwargs:
            try:
                result = invoke_slot(fn, fv, args)
            except TypeError as e:
                return ValidationResult(
                    passed=False,
                    ast_valid=True,
                    type_correct=True,
                    execution_ok=False,
                    error_message=f"Signature mismatch: {e}",
                    failure_kind="signature_mismatch",
                )
        else:
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
                    failure_kind="signature_mismatch",
                )
            result = fn(*args, **kwargs)
    except Exception:
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=True,
            execution_ok=False,
            error_message=traceback.format_exc(),
            failure_kind="execution_error",
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
                    failure_kind="type_mismatch",
                )
            if set(result.keys()) != set(output_names):
                return ValidationResult(
                    passed=False,
                    ast_valid=True,
                    type_correct=False,
                    execution_ok=True,
                    error_message=f"STATEMENT_BLOCK dict keys mismatch. expected={output_names} got={list(result.keys())}",
                    failure_kind="shape_mismatch",
                )
        else:
            if "inline:if_test" in hints:
                # Replaces `if ...: #> ...`; generated slot must produce a bool condition.
                if not isinstance(result, bool):
                    return ValidationResult(
                        passed=False,
                        ast_valid=True,
                        type_correct=False,
                        execution_ok=True,
                        error_message=f"Inline if-test slot must return bool; got {type(result).__name__}",
                        failure_kind="type_mismatch",
                    )
            elif "inline:return" in hints:
                # Replaces `return ... #> ...`; return value is validated below via expected_type.
                pass
            elif "inline:expr" in hints:
                # Replaces bare expression `... #> ...`; expression value is intentionally ignored.
                pass
            elif result is not None:
                # Side-effect blocks are allowed to return None.
                return ValidationResult(
                    passed=False,
                    ast_valid=True,
                    type_correct=True,
                    execution_ok=True,
                    error_message=f"STATEMENT_BLOCK with no output_names must return None; got {type(result).__name__}",
                    failure_kind="type_mismatch",
                )

    # Value to type-check: for STATEMENT_BLOCK with one named output, validate the inner value.
    value_for_typecheck: Any = result
    if (
        slot_category == SlotCategory.STATEMENT_BLOCK
        and output_names
        and len(output_names) == 1
        and isinstance(result, dict)
    ):
        key = output_names[0]
        if key in result:
            value_for_typecheck = result[key]

    if (
        expected_type is not type(None)
        and isinstance(value_for_typecheck, str)
        and value_for_typecheck == ""
        and any(isinstance(a, str) and a.strip() for a in (*args, *kwargs.values()))
    ):
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=False,
            execution_ok=True,
            error_message=(
                "Empty string result for non-empty string input; "
                "expected a non-empty conversion or a raised error."
            ),
            failure_kind="empty_output",
        )

    if _str_identity_passthrough_failure(
        slot_category=slot_category,
        expected_type=expected_type,
        sample_input=sample_input,
        value_for_typecheck=value_for_typecheck,
    ):
        return ValidationResult(
            passed=False,
            ast_valid=True,
            type_correct=False,
            execution_ok=True,
            error_message=(
                "Result equals non-empty input string; expected a transformed value or a raised error."
            ),
            failure_kind="identity_return",
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
        if not _validate_callable_shape(value_for_typecheck):
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=False,
                execution_ok=True,
                error_message=f"Expected a callable result; got {type(value_for_typecheck).__name__}",
                failure_kind="type_mismatch",
            )
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )

    if _should_use_typeadapter_for_expected_type(expected_type):
        ok, err = _validate_value_with_typeadapter(
            value_for_typecheck,
            expected_type,
            globals_namespace=typeadapter_globals,
        )
        if not ok:
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=False,
                execution_ok=True,
                error_message=err,
                failure_kind="type_mismatch",
            )
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )

    if expected_type is Any:
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )

    # Domain classes and plain Python types (no pydantic / no generic origin).
    if isinstance(expected_type, type):
        if not isinstance(value_for_typecheck, expected_type):
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=False,
                execution_ok=True,
                error_message=(
                    f"Returned {type(value_for_typecheck).__name__}, expected {expected_type.__name__}"
                ),
                failure_kind="type_mismatch",
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
    spec: Optional[GenerationSpec] = None,
) -> ValidationResult:
    """
    Validate generated slot implementation:
    - AST parse + ensure one function def
    - execute with sample_input (if enable_execution)
    - enforce STATEMENT_BLOCK return dict keys
    - enforce formal_constraints presence (verbatim substring match)
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

    primary_name: str | None = None
    if isinstance(tree, ast.Module):
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                primary_name = node.name
                break
    if primary_name is None:
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef):
                primary_name = n.name
                break
    if primary_name is None:
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
        if spec is not None and getattr(spec, "execution_namespace", None):
            ns.update(spec.execution_namespace)
        exec(compile(source, "<generated>", "exec"), ns)
        fn = ns.get(primary_name)
        if fn is None or not callable(fn) or isinstance(fn, type):
            return ValidationResult(
                passed=False,
                ast_valid=True,
                type_correct=True,
                execution_ok=False,
                error_message=f"No callable named {primary_name!r} in compiled source",
            )
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
    usage_hints = spec.slot_spec.usage_hints if spec and spec.slot_spec else []

    effective_type = spec.expected_type if spec is not None else expected_type
    effective_sample = spec.sample_input if spec is not None and spec.sample_input is not None else sample_input
    ta_globals = spec.execution_namespace if spec is not None else None

    fv = list(spec.slot_spec.free_variables) if spec and spec.slot_spec else None

    # An effectful slot's generated function declares an ``fx`` parameter and emits
    # effects via it. Detect it (only when the effects subsystem is enabled) so the
    # executor injects ``fx`` and skips return-type checks.
    effectful = False
    try:
        from semipy.agents.config import get_config
        from semipy.effects.inject import fn_is_effectful

        if getattr(get_config(), "effects_enabled", False):
            effectful = fn_is_effectful(fn)
    except Exception:
        effectful = False

    return _validate_basic_execution(
        fn=fn,
        expected_type=effective_type,
        sample_input=effective_sample,
        slot_category=slot_category,
        output_names=output_names,
        typeadapter_globals=ta_globals,
        free_variables=fv,
        usage_hints=usage_hints,
        effectful=effectful,
    )


def validate_boundary(
    source: str,
    spec: Optional[GenerationSpec] = None,
    *,
    expected_type: Any = None,
) -> ValidationResult:
    """Stage 1 validation: AST correctness, function presence, signature, shape.

    Returns a ValidationResult with failure_kind set to one of:
      "syntax_error"       - AST parse failed
      "no_function"        - no top-level function definition found
      "signature_mismatch" - function won't accept the slot's free_variables
      "shape_mismatch"     - STATEMENT_BLOCK: return dict keys don't match output_names
    """
    source = _extract_function_source(source)
    if not source.strip():
        return ValidationResult(
            passed=False, ast_valid=False, type_correct=False, execution_ok=False,
            error_message="No Python code found in response",
            failure_kind="syntax_error",
        )
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ValidationResult(
            passed=False, ast_valid=False, type_correct=False, execution_ok=False,
            error_message=f"Syntax error: {e}",
            failure_kind="syntax_error",
        )
    # Find primary function definition
    primary_name: str | None = None
    for node in (tree.body if isinstance(tree, ast.Module) else []):
        if isinstance(node, ast.FunctionDef):
            primary_name = node.name
            break
    if primary_name is None:
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef):
                primary_name = n.name
                break
    if primary_name is None:
        return ValidationResult(
            passed=False, ast_valid=False, type_correct=False, execution_ok=False,
            error_message="No function definition found",
            failure_kind="no_function",
        )
    # Signature check (quick structural check before execution)
    fv = list(spec.slot_spec.free_variables) if spec and spec.slot_spec else []
    sample_input = spec.sample_input if spec else None
    if fv and sample_input:
        args = tuple(sample_input.get("args", ()) or ())
        if len(fv) != len(args):
            return ValidationResult(
                passed=False, ast_valid=True, type_correct=True, execution_ok=False,
                error_message=f"Slot has {len(fv)} free variables but sample_input has {len(args)} args",
                failure_kind="signature_mismatch",
            )
    # Formal constraint check
    formal_constraints = (spec.slot_spec.formal_constraints if spec and spec.slot_spec else []) if spec else []
    constraint_err = _validate_formal_constraints(source, formal_constraints)
    if constraint_err:
        return ValidationResult(
            passed=False, ast_valid=True, type_correct=True, execution_ok=False,
            error_message=constraint_err,
            failure_kind="signature_mismatch",
        )
    return ValidationResult(
        passed=True, ast_valid=True, type_correct=True, execution_ok=True,
        error_message="",
    )


def validate_sandbox(
    source: str,
    spec: Optional[GenerationSpec] = None,
    *,
    expected_type: Any = None,
    sample_input: Optional[dict[str, Any]] = None,
) -> ValidationResult:
    """Stage 2 validation: compile, execute with sample inputs, type-check result.

    Returns a ValidationResult with failure_kind set to one of:
      "execution_error"  - exception raised during function execution
      "type_mismatch"    - return value type doesn't match expected_type
      "empty_output"     - empty string returned for non-empty string input
      "identity_return"  - output equals input (str identity passthrough)
    """
    source = _extract_function_source(source)
    if not source.strip():
        return ValidationResult(
            passed=False, ast_valid=False, type_correct=False, execution_ok=False,
            error_message="No Python code found",
            failure_kind="execution_error",
        )
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ValidationResult(
            passed=False, ast_valid=False, type_correct=False, execution_ok=False,
            error_message=f"Syntax error: {e}",
            failure_kind="execution_error",
        )
    primary_name = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            primary_name = n.name
            break
    if primary_name is None:
        return ValidationResult(
            passed=False, ast_valid=False, type_correct=False, execution_ok=False,
            error_message="No function definition found",
            failure_kind="execution_error",
        )
    try:
        ns: dict[str, Any] = {}
        if spec and getattr(spec, "execution_namespace", None):
            ns.update(spec.execution_namespace)
        exec(compile(source, "<generated>", "exec"), ns)
        fn = ns.get(primary_name)
        if fn is None or not callable(fn) or isinstance(fn, type):
            return ValidationResult(
                passed=False, ast_valid=True, type_correct=True, execution_ok=False,
                error_message=f"No callable named {primary_name!r} found after compile",
                failure_kind="execution_error",
            )
    except Exception:
        return ValidationResult(
            passed=False, ast_valid=True, type_correct=True, execution_ok=False,
            error_message=traceback.format_exc(),
            failure_kind="execution_error",
        )

    eff_type = (spec.expected_type if spec else None) or expected_type or type(None)
    eff_sample = (spec.sample_input if spec else None) or sample_input
    slot_category = spec.slot_spec.expected_category if spec and spec.slot_spec else None
    output_names = spec.slot_spec.output_names if spec and spec.slot_spec else []
    usage_hints = spec.slot_spec.usage_hints if spec and spec.slot_spec else []
    fv = list(spec.slot_spec.free_variables) if spec and spec.slot_spec else None
    ta_globals = spec.execution_namespace if spec else None

    result = _validate_basic_execution(
        fn=fn,
        expected_type=eff_type,
        sample_input=eff_sample,
        slot_category=slot_category,
        output_names=output_names,
        typeadapter_globals=ta_globals,
        free_variables=fv,
        usage_hints=usage_hints,
    )
    if result.passed:
        return result
    # Map error message patterns to typed failure_kind
    msg = result.error_message or ""
    if "Signature mismatch" in msg or "signature" in msg.lower():
        kind = "execution_error"
    elif "Empty string result" in msg or "empty" in msg.lower():
        kind = "empty_output"
    elif "Result equals non-empty input" in msg or "identity" in msg.lower():
        kind = "identity_return"
    elif "STATEMENT_BLOCK" in msg and "keys" in msg:
        kind = "type_mismatch"
    elif not result.type_correct:
        kind = "type_mismatch"
    else:
        kind = "execution_error"
    return ValidationResult(
        passed=False,
        ast_valid=result.ast_valid,
        type_correct=result.type_correct,
        execution_ok=result.execution_ok,
        error_message=msg,
        failure_kind=kind,
    )


def verify_runtime_execution(
    *,
    fn: Any,
    expected_type: Any,
    sample_input: Optional[dict[str, Any]],
    slot_category: SlotCategory | None,
    output_names: list[str],
    enable_execution: bool = True,
    free_variables: list[str] | None = None,
    usage_hints: list[str] | None = None,
) -> ValidationResult:
    """
    Run execution + type checks for an already-loaded dispatch function (REUSE path).

    Skips AST/source parsing and formal-constraint checks; mirrors ``validate`` execution
    behavior for the given ``sample_input``.
    """
    if not enable_execution:
        return ValidationResult(
            passed=True,
            ast_valid=True,
            type_correct=True,
            execution_ok=True,
            error_message="",
        )
    # Use the function's own __globals__ as the TypeAdapter namespace so user-defined
    # types that were seeded into the dispatch module's exec namespace are resolvable.
    fn_globals = getattr(fn, "__globals__", None)

    # A reused effectful function declares an ``fx`` parameter and needs a bound
    # shadow world for its reads to return real data. The standard verify can supply
    # neither, so skip it here -- the reuse effect gate (_run_reuse_effect_gate)
    # re-runs the function against a real shadow and verifies the effect invariants.
    try:
        from semipy.agents.config import get_config
        from semipy.effects.inject import fn_is_effectful

        if getattr(get_config(), "effects_enabled", False) and fn_is_effectful(fn):
            return ValidationResult(
                passed=True, ast_valid=True, type_correct=True, execution_ok=True,
                error_message="",
            )
    except Exception:
        pass

    return _validate_basic_execution(
        fn=fn,
        expected_type=expected_type,
        sample_input=sample_input,
        slot_category=slot_category,
        output_names=output_names,
        typeadapter_globals=fn_globals,
        free_variables=free_variables,
        usage_hints=usage_hints,
    )

