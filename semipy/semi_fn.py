from __future__ import annotations

import ast
import hashlib
import inspect
import linecache
from pathlib import Path
from typing import Any, Optional

from semipy.agents.config import get_config
from semipy.lowering import _make_slot_id
from semipy.slot_resolver import execute_slot
from semipy.types import (
    SlotCategory,
    SlotSpec,
    SemiCallSite,
    compute_spec_equivalence_key,
)


def _sha16(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _normalize_filename(path: str) -> str:
    if not path or path == "<unknown>":
        return path
    try:
        import os

        return str(os.path.abspath(path))
    except Exception:
        return path


def _get_call_frame_and_site(depth: int = 2) -> tuple[Optional[inspect.FrameInfo], SemiCallSite]:
    """
    Identify the call site by walking back 'depth' frames.
    Intended for standalone semi() diagnostics + slot identity.
    """
    frame = inspect.currentframe()
    try:
        if frame is None:
            return None, SemiCallSite(filename="<unknown>", lineno=0, func_qualname="")
        f = frame
        for _ in range(depth):
            if f is None or f.f_back is None:
                break
            f = f.f_back
        if f is None:
            return None, SemiCallSite(filename="<unknown>", lineno=0, func_qualname="")
        filename = _normalize_filename(f.f_code.co_filename or "<unknown>")
        lineno = f.f_lineno or 0
        # If inside a method, prefer the runtime self class for a stable qualname.
        qualname = f.f_code.co_name or ""
        self_obj = f.f_locals.get("self")
        if self_obj is not None:
            qualname = f"{self_obj.__class__.__name__}.{qualname}"
        return f, SemiCallSite(filename=filename, lineno=lineno, func_qualname=qualname)
    finally:
        del frame
    return None, SemiCallSite(filename="<unknown>", lineno=0, func_qualname="")


def _extract_semi_template_from_source_line(
    *,
    source_line: str,
    globals_ns: dict[str, Any],
    locals_ns: dict[str, Any],
) -> tuple[Optional[str], list[str], dict[str, Any]]:
    """
    Extract a stable template + its formatted values from a single-line semi(...) call.
    Returns (spec_text_template, free_variable_keys, runtime_values).
    """
    try:
        tree = ast.parse(source_line.strip())
    except SyntaxError:
        return None, [], {}

    semi_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "semi":
            if node.args:
                semi_calls.append(node)
    if not semi_calls:
        return None, [], {}

    # If multiple semi() calls exist in the line, we only support the simplest case:
    # the first one (most common in notebook lambdas).
    call = semi_calls[0]
    arg0 = call.args[0]
    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
        # Plain string: can't recover template variables.
        return arg0.value, [], {}

    if not isinstance(arg0, ast.JoinedStr):
        return None, [], {}

    literal_parts: list[str] = []
    formatted_expr_nodes: list[ast.AST] = []
    for v in arg0.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            literal_parts.append(v.value)
        elif isinstance(v, ast.FormattedValue):
            idx = len(formatted_expr_nodes)
            key = f"v{idx}"
            literal_parts.append("{" + key + "}")
            formatted_expr_nodes.append(v.value)
        else:
            return None, [], {}

    free_keys: list[str] = [f"v{i}" for i in range(len(formatted_expr_nodes))]
    spec_text_template = "".join(literal_parts)

    runtime_values: dict[str, Any] = {}
    for i, expr_node in enumerate(formatted_expr_nodes):
        key = f"v{i}"
        try:
            runtime_values[key] = eval(compile(ast.Expression(expr_node), "<semi_expr>", "eval"), globals_ns, locals_ns)  # noqa: S307
        except Exception:
            # If evaluation fails, degrade gracefully to use empty runtime input.
            runtime_values[key] = None

    return spec_text_template, free_keys, runtime_values


def _extract_semi_template_by_embedded_scalar(
    *,
    prompt: str,
    frame_locals: dict[str, Any],
) -> tuple[Optional[str], list[str], dict[str, Any]]:
    """
    Fallback when we cannot recover the f-string template from source code
    (common in notebooks/lambda frames).

    Heuristic:
    - find a scalar local whose str(value) is embedded in the prompt
    - replace the first occurrence with a placeholder {v0}
    - treat that scalar as the runtime input for v0
    """
    if not prompt or not frame_locals:
        return None, [], {}

    # Avoid series/arrays; we want stable scalar embeddings.
    scalar_candidates: list[tuple[int, Any, str]] = []
    for _k, v in frame_locals.items():
        if v is None:
            continue
        # Keep this duck-typed but conservative.
        if isinstance(v, (str, int, float, bool)):
            s = str(v)
        else:
            # numpy scalar types often format nicely; keep only "simple" ones
            try:
                import numpy as np  # type: ignore

                if isinstance(v, np.generic):
                    s = str(v.item())
                else:
                    continue
            except Exception:
                continue

        if not s:
            continue
        if len(s) < 2:
            continue
        if s in prompt:
            scalar_candidates.append((len(s), v, s))

    if not scalar_candidates:
        return None, [], {}

    scalar_candidates.sort(key=lambda t: t[0], reverse=True)
    _len, v0, s0 = scalar_candidates[0]

    template = prompt.replace(s0, "{v0}", 1)
    return template, ["v0"], {"v0": v0}


def _semi_standalone(prompt: str, *, expected_type: Any = None) -> Any:
    frame, call_site = _get_call_frame_and_site(depth=3)
    spec_text = prompt
    start_abs = call_site.lineno
    filename = call_site.filename
    func_qualname = call_site.func_qualname

    globals_ns: dict[str, Any] = {}
    locals_ns: dict[str, Any] = {}
    if frame is not None:
        globals_ns = frame.f_globals
        locals_ns = frame.f_locals

    # Try to extract an f-string template from the actual call-site code.
    source_line = ""
    if filename and start_abs:
        source_line = linecache.getline(filename, start_abs).strip()

    extracted_spec_text, extracted_free_keys, extracted_runtime_values = _extract_semi_template_from_source_line(
        source_line=source_line,
        globals_ns=globals_ns,
        locals_ns=locals_ns,
    )
    if extracted_spec_text is not None:
        spec_text = extracted_spec_text
        runtime_values = extracted_runtime_values
        free_variable_keys = extracted_free_keys
    else:
        # Heuristic fallback: infer {v0} by matching an in-scope scalar value embedded in the prompt.
        h_template, h_keys, h_values = _extract_semi_template_by_embedded_scalar(
            prompt=prompt,
            frame_locals=locals_ns,
        )
        if h_template is not None:
            spec_text = h_template
            runtime_values = h_values
            free_variable_keys = h_keys
        else:
            runtime_values = {}
            free_variable_keys = []

    expected = expected_type if expected_type is not None else type(None)
    spec_hash = _sha16(spec_text)
    spec_equivalence_key = compute_spec_equivalence_key(
        spec_text,
        free_variable_keys,
        expected,
        expected_category=SlotCategory.EXPRESSION_STANDALONE,
        output_names=[],
    )
    slot_id = _make_slot_id(filename, func_qualname, 0, f"{spec_text}\0{spec_equivalence_key}")

    control_context = "method" if "." in (func_qualname or "") else "top_level"

    slot_spec = SlotSpec(
        slot_id=slot_id,
        source_span=(filename, start_abs, start_abs),
        spec_text=spec_text,
        spec_hash=spec_hash,
        spec_equivalence_key=spec_equivalence_key,
        free_variables=free_variable_keys,
        control_context=control_context,
        expected_category=SlotCategory.EXPRESSION_STANDALONE,
        expected_type=expected,
        output_names=[],
        formal_constraints=[],
        usage_hints=[],
        enclosing_function_source="",
        enclosing_function_qualname=func_qualname,
    )

    config = get_config()
    cache_dir = Path(config.cache_dir)

    # Vectorization layer: if the slot input is a pandas-like Series, compute once on uniques.
    # This prevents generating/validating per row when users write semi(f"... {series} ...").
    # We only vectorize when exactly one slot input is collection-like.
    collection_inputs = [
        k
        for k, v in runtime_values.items()
        if v is not None and hasattr(v, "unique") and callable(getattr(v, "unique")) and not isinstance(v, (str, bytes))
    ]
    if len(collection_inputs) == 1:
        col_key = collection_inputs[0]
        col_val = runtime_values[col_key]
        try:
            uniques = list(col_val.unique())
        except Exception:
            uniques = []

        mapping: dict[Any, Any] = {}
        for u in uniques:
            rv = dict(runtime_values)
            rv[col_key] = u
            mapping[u] = execute_slot(slot_spec=slot_spec, runtime_values=rv, source_file=filename, cache_dir=cache_dir)

        # Try Series-like map first; fall back to list comprehension.
        try:
            if hasattr(col_val, "map") and callable(getattr(col_val, "map")):
                return col_val.map(mapping)
        except Exception:
            pass
        try:
            return [mapping.get(v) for v in list(col_val)]
        except Exception:
            return mapping.get(col_val)

    return execute_slot(
        slot_spec=slot_spec,
        runtime_values=runtime_values,
        source_file=filename,
        cache_dir=cache_dir,
    )


class SemiProxy:
    """
    Standalone semi() entry point.
    Inline semi(...) inside @semiformal is handled by the scaffold produced at decoration time.
    """

    def __call__(
        self,
        prompt: str,
        *,
        expected_type: Optional[Any] = None,
        require_tools: bool = False,
        **_kwargs: Any,
    ) -> Any:
        _ = require_tools  # require_tools is a future knob; standalone implementation does not branch.
        return _semi_standalone(prompt, expected_type=expected_type)


semi = SemiProxy()

