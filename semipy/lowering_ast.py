from __future__ import annotations

import ast
import builtins
import re
from typing import Any

# Names from the builtins module are never slot parameters (avoid len, range, max, ...).
_BUILTIN_NAMES: frozenset[str] = frozenset(vars(builtins))

# usage_hints markers for end-of-line #> specs (not lines that start with #>)
INLINE_ASSIGN = "inline:assign"
INLINE_IF_TEST = "inline:if_test"
INLINE_EXPR = "inline:expr"
INLINE_RETURN = "inline:return"


def _is_hash_arrow(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#>") or stripped.startswith("# >")


def _is_skeleton_placeholder_line(line: str) -> bool:
    """True for a line that is only `#` after lstrip (e.g. strip_skeleton_lines from `#<`)."""
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return False
    if stripped.startswith("#<") or stripped.startswith("#>") or stripped.startswith("# >"):
        return False
    return stripped[1:].strip() == ""


def _is_slot_anchor_line(line: str) -> bool:
    """True if the line contains an ellipsis assignment (``name = ...``) or a ``semi()`` call.

    These are slot output anchors that delimit separate slot regions within
    a function.  ``#>`` blocks separated by a slot anchor belong to different
    slots and must not be merged.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(r"\w+\s*=\s*\.\.\.", stripped):
        return True
    if "semi(" in stripped:
        return True
    return False


def _collect_hash_arrow_block_ranges(source_lines: list[str]) -> list[tuple[int, int]]:
    """0-based inclusive (start, last_hash_arrow) per logical #> block.

    Lines that are only `#` (skeleton placeholders between `#<` and code) sit between
    `#>` lines without splitting the block.

    After initial collection, adjacent blocks are **merged** when the lines
    between them contain no slot anchors (no ``name = ...`` or ``semi()``).
    This ensures a promoted ``#<`` line (now ``#>``) that is separated from
    the main ``#>`` group by regular code still belongs to the same slot.
    """
    raw_blocks: list[tuple[int, int]] = []
    i = 0
    n = len(source_lines)
    while i < n:
        if not _is_hash_arrow(source_lines[i]):
            i += 1
            continue
        start = i
        j = i
        last_hash_arrow = i
        while j < n:
            line = source_lines[j]
            if _is_hash_arrow(line):
                last_hash_arrow = j
                j += 1
            elif _is_skeleton_placeholder_line(line):
                j += 1
            else:
                break
        raw_blocks.append((start, last_hash_arrow))
        i = j

    if len(raw_blocks) <= 1:
        return raw_blocks

    merged: list[tuple[int, int]] = [raw_blocks[0]]
    for blk in raw_blocks[1:]:
        prev_start, prev_end = merged[-1]
        curr_start, curr_end = blk
        gap_has_anchor = any(
            _is_slot_anchor_line(source_lines[k])
            for k in range(prev_end + 1, curr_start)
        )
        if gap_has_anchor:
            merged.append(blk)
        else:
            merged[-1] = (prev_start, curr_end)
    return merged


def _inline_hash_arrow_spec(line: str) -> str | None:
    """Spec text after inline ``#>`` or ``#`` + optional space + ``>`` when the line is not a #>-only line."""
    if _is_hash_arrow(line):
        return None
    idx = line.find("#>")
    if idx >= 0:
        return line[idx + 2 :].strip() or None
    m = re.search(r"#\s*>", line)
    if m is None:
        return None
    return line[m.end() :].strip() or None


def _is_ellipsis_constant(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def _ellipsis_assign_targets(stmt: ast.Assign) -> list[str]:
    out: list[str] = []
    if not isinstance(stmt.value, ast.Constant) or stmt.value.value is not Ellipsis:
        return out
    for t in stmt.targets:
        if isinstance(t, ast.Name):
            out.append(t.id)
    return out


def _find_stmt_at_rel_line(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef, rel_line: int
) -> ast.stmt | None:
    return _find_stmt_at_rel_line_in_list(fn_def.body, rel_line)


def _find_stmt_at_rel_line_in_list(stmts: list[ast.stmt], rel_line: int) -> ast.stmt | None:
    for stmt in stmts:
        if getattr(stmt, "lineno", 0) == rel_line:
            return stmt
        if isinstance(stmt, ast.If):
            r = _find_stmt_at_rel_line_in_list(stmt.body, rel_line)
            if r is not None:
                return r
            r = _find_stmt_at_rel_line_in_list(stmt.orelse, rel_line)
            if r is not None:
                return r
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            r = _find_stmt_at_rel_line_in_list(stmt.body, rel_line)
            if r is not None:
                return r
            r = _find_stmt_at_rel_line_in_list(stmt.orelse, rel_line)
            if r is not None:
                return r
        if isinstance(stmt, ast.While):
            r = _find_stmt_at_rel_line_in_list(stmt.body, rel_line)
            if r is not None:
                return r
            r = _find_stmt_at_rel_line_in_list(stmt.orelse, rel_line)
            if r is not None:
                return r
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            r = _find_stmt_at_rel_line_in_list(stmt.body, rel_line)
            if r is not None:
                return r
        if isinstance(stmt, ast.Try):
            r = _find_stmt_at_rel_line_in_list(stmt.body, rel_line)
            if r is not None:
                return r
            for h in stmt.handlers:
                r = _find_stmt_at_rel_line_in_list(h.body, rel_line)
                if r is not None:
                    return r
            r = _find_stmt_at_rel_line_in_list(stmt.orelse, rel_line)
            if r is not None:
                return r
            r = _find_stmt_at_rel_line_in_list(stmt.finalbody, rel_line)
            if r is not None:
                return r
    return None


def _replace_if_test_at_line(fn_def: ast.FunctionDef, rel_line: int, call_expr: ast.expr) -> None:
    def walk(stmts: list[ast.stmt]) -> bool:
        for st in stmts:
            if isinstance(st, ast.If) and getattr(st, "lineno", 0) == rel_line:
                st.test = call_expr
                return True
            if isinstance(st, ast.If):
                if walk(st.body) or walk(st.orelse):
                    return True
        return False

    walk(fn_def.body)


def _replace_expr_value_at_line(fn_def: ast.FunctionDef, rel_line: int, call_expr: ast.expr) -> None:
    def walk(stmts: list[ast.stmt]) -> bool:
        for st in stmts:
            if isinstance(st, ast.Expr) and getattr(st, "lineno", 0) == rel_line:
                st.value = call_expr
                return True
            if isinstance(st, ast.If):
                if walk(st.body) or walk(st.orelse):
                    return True
            if isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
                if walk(st.body) or walk(st.orelse):
                    return True
            if isinstance(st, (ast.With, ast.AsyncWith)):
                if walk(st.body):
                    return True
            if isinstance(st, ast.Try):
                if walk(st.body) or walk(st.orelse) or walk(st.finalbody):
                    return True
                for h in st.handlers:
                    if walk(h.body):
                        return True
        return False

    walk(fn_def.body)


def _replace_return_value_at_line(fn_def: ast.FunctionDef, rel_line: int, call_expr: ast.expr) -> None:
    def walk(stmts: list[ast.stmt]) -> bool:
        for st in stmts:
            if isinstance(st, ast.Return) and getattr(st, "lineno", 0) == rel_line:
                st.value = call_expr
                return True
            if isinstance(st, ast.If):
                if walk(st.body) or walk(st.orelse):
                    return True
            if isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
                if walk(st.body) or walk(st.orelse):
                    return True
            if isinstance(st, (ast.With, ast.AsyncWith)):
                if walk(st.body):
                    return True
            if isinstance(st, ast.Try):
                if walk(st.body) or walk(st.orelse) or walk(st.finalbody):
                    return True
                for h in st.handlers:
                    if walk(h.body):
                        return True
        return False

    walk(fn_def.body)


def _splice_assign_replacement(
    stmts: list[ast.stmt], rel_line: int, replace_count: int, new_stmts: list[ast.stmt]
) -> bool:
    i = 0
    while i < len(stmts):
        st = stmts[i]
        if getattr(st, "lineno", 0) == rel_line and isinstance(st, ast.Assign):
            stmts[i : i + replace_count] = new_stmts
            return True
        if isinstance(st, ast.If):
            if _splice_assign_replacement(st.body, rel_line, replace_count, new_stmts):
                return True
            if _splice_assign_replacement(st.orelse, rel_line, replace_count, new_stmts):
                return True
        if isinstance(st, (ast.For, ast.AsyncFor)):
            if _splice_assign_replacement(st.body, rel_line, replace_count, new_stmts):
                return True
            if _splice_assign_replacement(st.orelse, rel_line, replace_count, new_stmts):
                return True
        if isinstance(st, ast.While):
            if _splice_assign_replacement(st.body, rel_line, replace_count, new_stmts):
                return True
            if _splice_assign_replacement(st.orelse, rel_line, replace_count, new_stmts):
                return True
        if isinstance(st, (ast.With, ast.AsyncWith)):
            if _splice_assign_replacement(st.body, rel_line, replace_count, new_stmts):
                return True
        if isinstance(st, ast.Try):
            if _splice_assign_replacement(st.body, rel_line, replace_count, new_stmts):
                return True
            for h in st.handlers:
                if _splice_assign_replacement(h.body, rel_line, replace_count, new_stmts):
                    return True
            if _splice_assign_replacement(st.orelse, rel_line, replace_count, new_stmts):
                return True
            if _splice_assign_replacement(st.finalbody, rel_line, replace_count, new_stmts):
                return True
        i += 1
    return False


def strip_skeleton_lines(source: str) -> str:
    """
    Replace each system-managed skeleton line with a blank `#` line (same length
    budget: one line per former skeleton line) so absolute line numbers stay
    aligned with prior runs. Two skeleton surfaces are stripped:

    - `#<` reasoning annotations (intent/given/by/unless/yields/verified)
    - `#?` open-decision forks (the surfaced silent choices)

    Both are derived, not user contract, so blanking them keeps `slot_id`, slot
    ordinals, and line numbers stable when a fork is added, edited, or resolved.
    User `#>` spec lines are unchanged.
    """
    lines = source.splitlines(keepends=True)
    result: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#<") or stripped.startswith("#?"):
            indent = len(line) - len(stripped)
            rest = line.rstrip("\r\n")
            ending = line[len(rest) :]
            result.append(line[:indent] + "#" + ending)
        else:
            result.append(line)
    return "".join(result)


def _strip_hash_arrow(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("#>"):
        return stripped[2:].strip()
    if stripped.startswith("# >"):
        return stripped[3:].strip()
    return stripped


def _relative_to_abs_lineno(first_lineno: int, rel_lineno: int) -> int:
    # ast lineno is 1-based relative to parsed source
    return first_lineno + rel_lineno - 1


def _abs_to_rel_lineno(first_lineno: int, abs_lineno: int) -> int:
    return abs_lineno - first_lineno + 1


def _assigned_names(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
    return out


def _loaded_names(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            out.add(n.id)
    return out


def _function_def_from_source(dedented_source: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(dedented_source)
    except SyntaxError:
        return None
    if not tree.body:
        return None
    first = tree.body[0]
    if isinstance(first, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return first
    return None


def _ordered_vars_from_fn(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef,
    names: set[str],
) -> list[str]:
    # Preserve declared function parameter order first, then deterministic sorting for any
    # additional locals/globals that appear in the surrounding region.
    param_names: list[str] = []
    param_names.extend([a.arg for a in fn_def.args.posonlyargs])
    param_names.extend([a.arg for a in fn_def.args.args])
    if fn_def.args.vararg is not None:
        param_names.append(fn_def.args.vararg.arg)
    param_names.extend([a.arg for a in fn_def.args.kwonlyargs])
    if fn_def.args.kwarg is not None:
        param_names.append(fn_def.args.kwarg.arg)

    ordered: list[str] = [n for n in param_names if n in names]
    remaining = names - set(ordered)
    ordered.extend(sorted(remaining))
    return ordered


def _bound_names_before(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef,
    until_rel_lineno: int,
) -> set[str]:
    bound: set[str] = set()
    for arg in fn_def.args.args:
        bound.add(arg.arg)
    for arg in fn_def.args.posonlyargs:
        bound.add(arg.arg)
    for arg in fn_def.args.kwonlyargs:
        bound.add(arg.arg)
    if fn_def.args.vararg:
        bound.add(fn_def.args.vararg.arg)
    if fn_def.args.kwarg:
        bound.add(fn_def.args.kwarg.arg)

    for stmt in fn_def.body:
        stmt_start = getattr(stmt, "lineno", 0) or 0
        stmt_end = getattr(stmt, "end_lineno", stmt_start) or stmt_start
        if stmt_end < until_rel_lineno and stmt_start > 0:
            bound |= _assigned_names(stmt)
        if stmt_end >= until_rel_lineno:
            break
    return bound


def _infer_expected_type_from_if_tests(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef,
    output_names: list[str],
) -> Any:
    target = set(output_names)
    for node in ast.walk(fn_def):
        if isinstance(node, ast.If):
            test_names = _loaded_names(node.test)
            if target & test_names:
                return bool
    return type(None)


def _infer_expected_type_for_output_names(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef,
    output_names: list[str],
    type_hints: dict[str, Any] | None,
) -> Any:
    # Prefer function return type when output is returned.
    if not output_names:
        return type(None)
    if len(output_names) != 1:
        # Multiple named locals; the slot returns a dict-shaped payload, not the enclosing
        # return type (e.g. RiskRow) as a single value.
        return Any
    if type_hints and "return" in type_hints:
        return_type = type_hints.get("return")
    else:
        return_type = None
    for node in ast.walk(fn_def):
        if isinstance(node, ast.Return) and node.value is not None:
            loaded = _loaded_names(node.value)
            if len(set(output_names) & loaded) >= 1 and return_type is not None:
                return return_type
    # If used in if-test => bool
    inferred = _infer_expected_type_from_if_tests(fn_def, output_names)
    return inferred


def _control_context_for_line(fn_def: ast.FunctionDef | ast.AsyncFunctionDef, rel_lineno: int, func_qualname: str) -> str:
    if "." in func_qualname:
        # method on a class (heuristic)
        method_ctx = True
    else:
        method_ctx = False

    enclosing_for: bool = False
    enclosing_if: bool = False
    for node in ast.walk(fn_def):
        if isinstance(node, ast.For) and getattr(node, "end_lineno", None) is not None:
            if node.lineno <= rel_lineno <= (node.end_lineno or node.lineno):
                enclosing_for = True
        if isinstance(node, ast.If) and getattr(node, "end_lineno", None) is not None:
            if node.lineno <= rel_lineno <= (node.end_lineno or node.lineno):
                enclosing_if = True
    if enclosing_for:
        return "for_loop"
    if enclosing_if:
        return "if_branch"
    return "method" if method_ctx else "top_level"


def _extract_formal_constraint_lines(
    source_lines: list[str],
    comment_indent: str,
    block_end_idx: int,
) -> list[str]:
    # Collect code lines right after the #> block that share the same indentation.
    # We exclude comment-only lines, blanks, and the next #> block.
    formal: list[str] = []
    i = block_end_idx + 1
    while i < len(source_lines):
        line = source_lines[i]
        if line.strip() == "":
            break
        if not line.startswith(comment_indent) and line.lstrip() != line:
            # indentation dropped; stop
            break
        if _is_hash_arrow(line):
            break
        stripped = line.lstrip()
        if stripped.startswith("#"):
            i += 1
            continue
        if not stripped:
            break
        # Control-flow lines are outside the slot output contract; do not treat them
        # as "hard constraint" lines to be preserved inside the generated slot body.
        if stripped.startswith(("return ", "raise ", "break ", "continue ")):
            break
        formal.append(stripped.rstrip())
        i += 1
    return formal


def _call_target_names_in_stmts(stmts: list[ast.stmt]) -> set[str]:
    """Names used as the callable in ``Name(...)`` calls (constructors, helpers), not slot outputs."""
    out: set[str] = set()
    for stmt in stmts:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                out.add(n.func.id)
    return out


def _is_placeholder_value(node: ast.AST | None) -> bool:
    """True when ``node`` is a trivial "empty of its type" initializer.

    These are placeholders a user writes so the skeleton parses before the ``#>``
    block fills them in (``result = None``, ``acc = []``, ``out = {}``,
    ``total = 0``, ``buf = bytearray()``), not real inputs carrying data.
    """
    if isinstance(node, ast.Constant):
        v = node.value
        if v is True:
            return False
        return v is None or v is False or v == "" or v == b"" or v == 0
    if isinstance(node, (ast.List, ast.Tuple)) and not node.elts:
        return True
    if isinstance(node, ast.Dict) and not node.keys:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return (
            node.func.id in ("list", "dict", "set", "tuple", "frozenset", "bytearray", "bytes")
            and not node.args
            and not node.keywords
        )
    return False


def _placeholder_init_names(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef,
    upto_rel_lineno: int,
) -> set[str]:
    """Top-level names whose last binding before ``upto_rel_lineno`` is a placeholder.

    A reassignment to a non-placeholder value clears the name: it now carries real
    data and is a genuine input, not a slot output. Annotation-only declarations
    (``result: Invoice``) are placeholders too.
    """
    state: dict[str, bool] = {}
    for stmt in fn_def.body:
        if (getattr(stmt, "lineno", 0) or 0) > upto_rel_lineno:
            break
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            state[stmt.targets[0].id] = _is_placeholder_value(stmt.value)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            state[stmt.target.id] = stmt.value is None or _is_placeholder_value(stmt.value)
    return {name for name, is_ph in state.items() if is_ph}


def _infer_output_names_for_statement_block(
    fn_def: ast.FunctionDef | ast.AsyncFunctionDef,
    block_end_rel_lineno: int,
    names_defined_before: set[str],
    max_statements: int = 4,
    exclude_names: set[str] | None = None,
) -> list[str]:
    after: list[ast.stmt] = []
    for stmt in fn_def.body:
        stmt_start = getattr(stmt, "lineno", 0) or 0
        if stmt_start > block_end_rel_lineno:
            after.append(stmt)
    if not after:
        return []
    region = after[:max_statements]
    ellipsis_targets: list[str] = []
    for stmt in region:
        if not isinstance(stmt, ast.Assign):
            continue
        val = stmt.value
        if not isinstance(val, ast.Constant) or val.value is not Ellipsis:
            continue
        for tgt in stmt.targets:
            if isinstance(tgt, ast.Name):
                ellipsis_targets.append(tgt.id)
    if ellipsis_targets:
        return ellipsis_targets
    loaded: set[str] = set()
    assigned: set[str] = set()
    for stmt in region:
        loaded |= _loaded_names(stmt)
        assigned |= _assigned_names(stmt)
    loaded -= _call_target_names_in_stmts(region)
    # Names assigned inside the region are not outputs of the #> block; they
    # are produced by subsequent formal code statements.
    out = sorted(loaded - names_defined_before - assigned)
    # Heuristic: avoid very obvious non-locals
    out = [n for n in out if n not in ("True", "False", "None")]
    if exclude_names:
        out = [n for n in out if n not in exclude_names]
    if not out:
        # Canonical idiom: ``result = <placeholder>; #> ...; return result``. The
        # placeholder var was excluded above as "defined before", but a name
        # initialized only to make the skeleton parse, then read after the block
        # (e.g. ``return result``), is the block's OUTPUT, not a real input.
        placeholders = _placeholder_init_names(fn_def, block_end_rel_lineno)
        cand = sorted((loaded & placeholders) - assigned)
        cand = [n for n in cand if n not in ("True", "False", "None")]
        if exclude_names:
            cand = [n for n in cand if n not in exclude_names]
        out = cand
    return out
