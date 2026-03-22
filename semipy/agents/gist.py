"""
AST-based gist builder: assemble a minimal standalone executable from user code
and a generated function for sandboxed validation.

Reuses patterns from agents/refs/example_glm.py and validator._extract_enclosing_statement.
"""
from __future__ import annotations

import ast
import inspect
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, get_args, get_origin

from semipy.agents.slot_call import bind_slot_arguments
from semipy.types import GenerationSpec


def _collect_referenced_types(spec: GenerationSpec) -> list[type]:
    """Types from expected_type and sample values that may need to be bound in an isolated gist."""
    seen: set[int] = set()
    out: list[type] = []

    def add_obj(o: Any) -> None:
        if isinstance(o, type):
            oid = id(o)
            if oid not in seen:
                seen.add(oid)
                out.append(o)

    def walk(t: Any) -> None:
        if isinstance(t, type):
            add_obj(t)
            return
        if get_origin(t) is not None:
            for a in get_args(t) or ():
                walk(a)

    walk(spec.expected_type)
    sample = spec.sample_input or {}
    for a in sample.get("args") or ():
        if isinstance(a, type):
            walk(a)
        else:
            add_obj(type(a))
    for v in (sample.get("kwargs") or {}).values():
        if isinstance(v, type):
            walk(v)
        else:
            add_obj(type(v))
    return out


def _type_defined_at_source_path(typ: type, abs_path: str) -> bool:
    try:
        tpath = os.path.abspath(os.path.normpath(inspect.getfile(typ)))
        return os.path.normcase(tpath) == os.path.normcase(os.path.abspath(abs_path))
    except Exception:
        return False


def _resolved_user_source_path(spec: GenerationSpec) -> str:
    """
    Absolute path to the user's module for ``importlib`` loading in gist subprocesses.

    Prefer ``execution_namespace['__file__']`` (reliable for ``python script.py``); fall
    back to ``call_site.filename`` and cwd-relative resolution.
    """
    ns = spec.execution_namespace or {}
    ef = ns.get("__file__")
    if isinstance(ef, str):
        try:
            ap = os.path.abspath(os.path.normpath(ef))
            if os.path.isfile(ap):
                return ap
        except Exception:
            pass
    path = spec.call_site.filename
    if not path or path == "<unknown>":
        return ""
    try:
        p = Path(path).expanduser()
        if p.is_file():
            return str(p.resolve())
        cand = Path.cwd() / p
        if cand.is_file():
            return str(cand.resolve())
    except Exception:
        pass
    try:
        ap = os.path.abspath(os.path.normpath(path))
        if os.path.isfile(ap):
            return ap
    except Exception:
        pass
    return ""


def _gist_user_types_preamble(spec: GenerationSpec) -> tuple[str, Optional[str]]:
    """
    Load the call-site module so gist subprocesses expose user-defined classes.

    Path is not embedded in source (avoids repr/quoting edge cases). The executor sets
    ``SEMIPY_GIST_USER_SOURCE`` on the subprocess environment to the absolute path.
    """
    abs_path = _resolved_user_source_path(spec)
    if not abs_path:
        return "", None
    names: list[str] = []
    for t in _collect_referenced_types(spec):
        if not isinstance(t, type):
            continue
        if not _type_defined_at_source_path(t, abs_path):
            continue
        n = getattr(t, "__name__", None)
        if isinstance(n, str) and n.isidentifier():
            names.append(n)
    names = sorted(set(names))
    if not names:
        return "", None
    lines = [
        "import importlib.util",
        "import os",
        "import pathlib",
        "_semipy_p = (os.environ.get('SEMIPY_GIST_USER_SOURCE') or '').strip()",
        "if _semipy_p and pathlib.Path(_semipy_p).is_file():",
        "    _semipy_spec = importlib.util.spec_from_file_location('_semipy_user_slot', _semipy_p)",
        "    _semipy_user_mod = importlib.util.module_from_spec(_semipy_spec)",
        "    _semipy_spec.loader.exec_module(_semipy_user_mod)",
        "else:",
        "    _semipy_user_mod = None",
    ]
    for n in names:
        lines.append(f"{n} = getattr(_semipy_user_mod, {repr(n)}, None)")
    return "\n".join(lines) + "\n\n", abs_path


def _expr_for_gist_invocation(value: Any) -> str:
    """
    Build a Python expression string for embedding in generated gist source.
    repr() of arbitrary objects (e.g. class instances) is not valid Python when pasted
    as a call argument; use literals for primitives and None for everything else.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, bytes):
        return repr(value)
    if isinstance(value, tuple):
        inner = ", ".join(_expr_for_gist_invocation(x) for x in value)
        if len(value) == 1:
            return f"({inner},)"
        return f"({inner})"
    if isinstance(value, list):
        return "[" + ", ".join(_expr_for_gist_invocation(x) for x in value) + "]"
    if isinstance(value, dict):
        parts = [f"{_expr_for_gist_invocation(k)}: {_expr_for_gist_invocation(v)}" for k, v in value.items()]
        return "{" + ", ".join(parts) + "}"
    return "None"


def _get_names_used(node: ast.AST) -> set[str]:
    """Collect all name ids read from an AST node (for dependency tracking)."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Load):
            names.add(n.id)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            names.add(n.value.id)
    return names


def _future_imports_first(imports: list[str]) -> list[str]:
    """Put any __future__ import lines first so they appear at the top of the gist."""
    future = [s for s in imports if "__future__" in s]
    rest = [s for s in imports if "__future__" not in s]
    return future + rest


def _get_code_snippet(source: str, node: ast.AST) -> str:
    """Return the source slice for a node if we have full source."""
    try:
        return ast.get_source_segment(source, node) or ""
    except Exception:
        return ""


def _extract_enclosing_statement(
    source_code: str,
    semi_call_lineno: int,
    first_lineno: int,
) -> Optional[str]:
    """Return the source of the top-level statement that contains the semi() call line, or None."""
    if not source_code.strip() or semi_call_lineno < first_lineno:
        return None
    rel = semi_call_lineno - first_lineno + 1
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


@dataclass
class Gist:
    """Assembled minimal executable: source code, function name, test invocation snippet."""

    source: str
    fn_name: str
    test_invocation: str
    upstream_deps: list[str] = field(default_factory=list)
    mocked_externals: list[str] = field(default_factory=list)
    user_source_path: Optional[str] = None


class GistBuilder:
    """
    Builds a runnable gist for sandbox validation: only the generated function,
    any imports present in the generated snippet, and a test invocation with
    sample data. Does not include user-file imports (e.g. semipy) or upstream
    context, so the gist runs in a minimal environment (e.g. E2B) without
    requiring the semiformal package.
    """

    def __init__(self, spec: GenerationSpec) -> None:
        self.spec = spec
        self.last_build_error: Optional[str] = None

    def build(self, generated_function_source: str) -> Optional[Gist]:
        """
        Assemble a minimal standalone script to test the generated function with
        real data flow: only imports from the generated snippet, the generated
        function, and test invocation (sample_input / variable_values from spec).
        Returns None if the generated source cannot be parsed or has no function.
        """
        self.last_build_error = None
        raw = _extract_function_source(generated_function_source)
        if not raw.strip():
            self.last_build_error = "empty generated source"
            return None

        import_lines, fn_source = _extract_imports_and_function_from_generated(raw)
        if not fn_source.strip():
            self.last_build_error = "no function definition in generated source"
            return None

        try:
            fn_tree = ast.parse(fn_source)
        except SyntaxError:
            self.last_build_error = "syntax error in generated function"
            return None
        gen_fn_name: str | None = None
        if isinstance(fn_tree, ast.Module):
            for node in fn_tree.body:
                if isinstance(node, ast.FunctionDef):
                    gen_fn_name = node.name
                    break
        if gen_fn_name is None:
            for n in ast.walk(fn_tree):
                if isinstance(n, ast.FunctionDef):
                    gen_fn_name = n.name
                    break
        if not gen_fn_name:
            self.last_build_error = "no function definition after parse"
            return None

        try:
            ns: dict[str, Any] = {}
            exec(compile(fn_source, "<gist_sig>", "exec"), ns)
            fn = ns.get(gen_fn_name)
            if fn is None or not callable(fn) or isinstance(fn, type):
                self.last_build_error = f"no callable named {gen_fn_name!r} in compiled gist source"
                return None
            sample = self.spec.sample_input or {}
            args = tuple(sample.get("args", ()) or ())
            kwargs = dict(sample.get("kwargs", {}) or {})
            fv = list(self.spec.slot_spec.free_variables) if self.spec.slot_spec else []
            if fv and len(fv) == len(args) and not kwargs:
                bind_slot_arguments(fn, fv, args)
            else:
                inspect.signature(fn).bind(*args, **kwargs)
            test_invocation = _build_test_invocation(self.spec, gen_fn_name, fn)
        except TypeError as e:
            self.last_build_error = (
                "Generated function signature does not accept the slot's sample call "
                f"(positional arity must match slot inputs). Detail: {e}"
            )
            return None
        except Exception as e:
            self.last_build_error = f"preflight compile/bind failed: {e}"
            return None

        lines: list[str] = []
        preamble, user_path_for_env = _gist_user_types_preamble(self.spec)
        if preamble.strip():
            lines.append(preamble.rstrip())
            lines.append("")
        if import_lines:
            lines.extend(import_lines)
            lines.append("")
        lines.append(fn_source)
        lines.append("")
        lines.append(test_invocation)

        return Gist(
            source="\n".join(lines),
            fn_name=gen_fn_name,
            test_invocation=test_invocation,
            upstream_deps=[],
            mocked_externals=[],
            user_source_path=user_path_for_env,
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


def _strip_future_imports(source: str) -> str:
    """Remove any line that is a __future__ import (so it is not duplicated in gist)."""
    out: list[str] = []
    for line in source.splitlines():
        s = line.strip()
        if s.startswith("from __future__"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _normalize_generated_function_source(fn_source: str) -> str:
    """
    Use only the first top-level function definition from the generated source.
    The full function is preserved (signature, return type annotation, body). We only
    drop leading module-level imports and __future__ lines so the gist never has
    __future__ or duplicate imports in the middle (which cause SyntaxError).
    """
    fn_source = _strip_future_imports(fn_source)
    if not fn_source.strip():
        return fn_source
    try:
        tree = ast.parse(fn_source)
    except SyntaxError:
        return fn_source
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            seg = _get_code_snippet(fn_source, node)
            if seg and seg.strip():
                return seg.strip()
            break
    return fn_source


def _extract_imports_and_function_from_generated(raw_source: str) -> tuple[list[str], str]:
    """
    From raw generated snippet, return (leading_import_lines, function_source).
    Leading imports are Import/ImportFrom before the first FunctionDef; the rest
    is the first function definition. Used so the gist only includes imports
    that the generated code needs (no user-file imports like semipy).
    """
    if not raw_source.strip():
        return [], ""
    try:
        tree = ast.parse(raw_source)
    except SyntaxError:
        return [], ""
    import_lines: list[str] = []
    function_source = ""
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            seg = _get_code_snippet(raw_source, node)
            if seg:
                import_lines.append(seg)
        elif isinstance(node, ast.FunctionDef):
            seg = _get_code_snippet(raw_source, node)
            if seg and seg.strip():
                function_source = seg.strip()
            break
    return _future_imports_first(import_lines), function_source


def _collect_upstream_snippets(
    file_source: str,
    func_source: str,
    semi_lineno: int,
    first_lineno: int,
    names_used: set[str],
) -> list[str]:
    """
    Collect source snippets that define the names used in the enclosing statement.
    Only module-level assignments are included (imports are handled separately in build()).
    Function-body statements are not pasted into the gist (they are invalid at module level).
    """
    snippets: list[str] = []
    try:
        file_tree = ast.parse(file_source)
    except SyntaxError:
        return []

    defined_in_module: set[str] = set()
    for node in ast.iter_child_nodes(file_tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in (node.names if hasattr(node, "names") else []):
                name = getattr(alias, "asname", None) or getattr(alias, "name", None)
                if name:
                    defined_in_module.add(name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined_in_module.add(t.id)
            seg = _get_code_snippet(file_source, node)
            if seg and any(
                isinstance(t, ast.Name) and t.id in names_used
                for t in node.targets
            ):
                snippets.append(seg)

    return snippets


def _build_test_invocation(
    spec: GenerationSpec,
    fn_name: str,
    fn: Any | None = None,
) -> str:
    """Build the test invocation line(s) from spec.sample_input or spec.variable_values."""
    marker = "__GIST_RESULT__"
    sample = spec.sample_input
    if sample and isinstance(sample, dict):
        args = tuple(sample.get("args", ()) or ())
        kwargs = dict(sample.get("kwargs", {}) or {})
        if args or kwargs:
            fv = list(spec.slot_spec.free_variables) if spec.slot_spec else []
            if (
                fn is not None
                and fv
                and len(fv) == len(args)
                and not kwargs
            ):
                by_name = dict(zip(fv, args))
                sig = inspect.signature(fn)
                parts = [
                    f"{name}={_expr_for_gist_invocation(by_name[name])}"
                    for name in sig.parameters
                    if name in by_name
                ]
                args_str = ", ".join(parts)
            elif fv and len(fv) == len(args) and not kwargs:
                args_str = ", ".join(
                    f"{k}={_expr_for_gist_invocation(v)}" for k, v in zip(fv, args)
                )
            else:
                args_str = ", ".join(_expr_for_gist_invocation(a) for a in args)
                if kwargs:
                    args_str += ", " + ", ".join(
                        f"{k}={_expr_for_gist_invocation(v)}" for k, v in kwargs.items()
                    )
            return f"{marker} = {fn_name}({args_str})\nprint({repr(marker)}, repr({marker}), flush=True)"
    variable_values = getattr(spec, "variable_values", None) or {}
    if variable_values:
        ordered = getattr(spec.template, "variable_names", []) if spec.template else []
        if ordered:
            vals = [variable_values.get(n, None) for n in ordered]
            args_str = ", ".join(_expr_for_gist_invocation(v) for v in vals)
            return f"{marker} = {fn_name}({args_str})\nprint({repr(marker)}, repr({marker}), flush=True)"
    return f"{marker} = {fn_name}()\nprint({repr(marker)}, repr({marker}), flush=True)"
