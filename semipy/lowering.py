from __future__ import annotations

import ast
import builtins
import hashlib
import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from semipy.types import (
    Decision,
    SlotCategory,
    SlotSpec,
    SemiCallSite,
    _stable_slot_hash,
    compute_spec_equivalence_key,
)

# Names from the builtins module are never slot parameters (avoid len, range, max, ...).
_BUILTIN_NAMES: frozenset[str] = frozenset(vars(builtins))


def _sha16(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _is_hash_arrow(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#>") or stripped.startswith("# >")


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
    return out


def _make_slot_id(filename: str, func_qualname: str, start_abs_lineno: int, spec_text: str) -> str:
    key = f"{filename}:{func_qualname}:{start_abs_lineno}:{spec_text}"
    return _stable_slot_hash(key)


def _make_slot_spec(
    *,
    filename: str,
    func_qualname: str,
    start_abs_lineno: int,
    end_abs_lineno: int,
    spec_text: str,
    free_variables: list[str],
    control_context: str,
    expected_category: SlotCategory,
    expected_type: Any,
    output_names: list[str],
    formal_constraints: list[str],
    usage_hints: list[str],
    enclosing_function_source: str,
    enclosing_function_qualname: str,
) -> SlotSpec:
    spec_hash = _sha16(spec_text)
    spec_equivalence_key = compute_spec_equivalence_key(
        spec_text,
        free_variables,
        expected_type,
        expected_category=expected_category,
        output_names=output_names,
    )
    slot_id = _make_slot_id(filename, func_qualname, start_abs_lineno, spec_text)
    source_span = (filename, start_abs_lineno, end_abs_lineno)
    return SlotSpec(
        slot_id=slot_id,
        source_span=source_span,
        spec_text=spec_text,
        spec_hash=spec_hash,
        spec_equivalence_key=spec_equivalence_key,
        free_variables=free_variables,
        control_context=control_context,
        expected_category=expected_category,
        expected_type=expected_type,
        output_names=output_names,
        formal_constraints=formal_constraints,
        usage_hints=usage_hints,
        enclosing_function_source=enclosing_function_source,
        enclosing_function_qualname=enclosing_function_qualname,
    )


def scan_informal_specs(
    source: str,
    filename: str,
    func_qualname: str,
    first_lineno: int,
    *,
    type_hints: dict[str, Any] | None = None,
    globals_ns: dict[str, Any] | None = None,
) -> list[SlotSpec]:
    """
    Parse a @semiformal function body for all open regions, in source order:
    - #> comment blocks → STATEMENT_BLOCK slots
    - semi(...) Call nodes → EXPRESSION slots
    If neither is found: return single FUNCTION_BODY slot for the whole body.
    """
    dedented = textwrap.dedent(source)
    source_lines = dedented.splitlines()
    try:
        tree = ast.parse(dedented)
    except SyntaxError:
        # Let the decorator compilation decide what to do; returning empty keeps errors localized.
        return []
    fn_def = _function_def_from_source(dedented)
    if fn_def is None:
        return []

    exclude_names: set[str] = set()
    if globals_ns:
        exclude_names |= set(globals_ns.keys())
    # Builtins shouldn't be treated as "outputs" of a #> block.
    try:
        import builtins as _builtins
        exclude_names |= set(dir(_builtins))
    except Exception:
        pass

    # Map of semi call lineno (abs) -> SlotSpec
    semi_slots: list[tuple[int, SlotSpec]] = []

    # Collect semi() call expressions for SlotSpec creation (EXPRESSION).
    for node in ast.walk(fn_def):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "semi":
            if not node.args:
                continue
            prompt_expr = node.args[0]
            spec_text = ""
            if isinstance(prompt_expr, ast.Constant) and isinstance(prompt_expr.value, str):
                spec_text = prompt_expr.value
            else:
                seg = ast.get_source_segment(dedented, prompt_expr)
                spec_text = seg.strip() if seg else ""

            # Resolve expected_type kwarg if present
            expected_type: Any = type(None)
            for kw in node.keywords:
                if kw.arg == "expected_type" and kw.value is not None:
                    expr_src = ast.get_source_segment(dedented, kw.value) or ""
                    if globals_ns and expr_src:
                        try:
                            expected_type = eval(expr_src, globals_ns)  # noqa: S307
                        except Exception:
                            expected_type = type(None)
                    break

            rel_lineno = getattr(node, "lineno", 1) or 1
            start_abs = _relative_to_abs_lineno(first_lineno, rel_lineno)
            end_abs = start_abs

            # Free variables: only names *read inside the prompt expression* (first arg).
            # Using (bound_before | loaded) for the whole semi() call incorrectly treated
            # every prior assignment in the function (e.g. parser, args) as slot inputs,
            # bloating signatures and gist invocations. Keywords like expected_type=str
            # are outside prompt_expr and must not add spurious parameters.
            loaded_in_prompt = _loaded_names(prompt_expr)
            free_var_set = loaded_in_prompt - {"semi"} - _BUILTIN_NAMES
            free_vars = _ordered_vars_from_fn(fn_def, free_var_set)

            control_context = _control_context_for_line(fn_def, rel_lineno, func_qualname)
            expected_category = SlotCategory.EXPRESSION

            usage_hints = []
            slot_spec = _make_slot_spec(
                filename=filename,
                func_qualname=func_qualname,
                start_abs_lineno=start_abs,
                end_abs_lineno=end_abs,
                spec_text=spec_text,
                free_variables=free_vars,
                control_context=control_context,
                expected_category=expected_category,
                expected_type=expected_type,
                output_names=[],
                formal_constraints=[],
                usage_hints=usage_hints,
                enclosing_function_source=source,
                enclosing_function_qualname=func_qualname,
            )
            semi_slots.append((start_abs, slot_spec))

    # Collect #> comment blocks for SlotSpec creation (STATEMENT_BLOCK).
    comment_slots: list[tuple[int, SlotSpec]] = []
    i = 0
    while i < len(source_lines):
        if not _is_hash_arrow(source_lines[i]):
            i += 1
            continue
        # contiguous block
        indent = source_lines[i][: len(source_lines[i]) - len(source_lines[i].lstrip())]
        block_start_idx = i
        j = i
        while j < len(source_lines) and _is_hash_arrow(source_lines[j]):
            j += 1
        block_end_idx = j - 1

        block_lines = source_lines[block_start_idx : block_end_idx + 1]
        spec_text = "\n".join(_strip_hash_arrow(l) for l in block_lines).strip()

        start_abs = first_lineno + block_start_idx
        end_abs = first_lineno + block_end_idx
        block_end_rel = _abs_to_rel_lineno(first_lineno, end_abs)

        bound_before = _bound_names_before(fn_def, block_end_rel + 1)
        output_names = _infer_output_names_for_statement_block(
            fn_def,
            block_end_rel,
            names_defined_before=bound_before,
            exclude_names=exclude_names,
        )

        formal_constraints = _extract_formal_constraint_lines(source_lines, indent, block_end_idx)

        # Only names already bound before the statement after the #> block are slot inputs.
        # Do not scan later statements: that pulls in builtins and unrelated names (semi, ticker, ax)
        # from loops below and blows up the generated signature.
        assigned_in_region: set[str] = set()
        for stmt in fn_def.body:
            stmt_lineno = getattr(stmt, "lineno", 0) or 0
            if stmt_lineno > block_end_rel:
                if stmt_lineno > block_end_rel + 20:
                    break
                assigned_in_region |= _assigned_names(stmt)
        free_var_set = bound_before - assigned_in_region - set(output_names) - _BUILTIN_NAMES
        free_vars = _ordered_vars_from_fn(fn_def, free_var_set)

        control_context = _control_context_for_line(fn_def, block_end_rel, func_qualname)
        usage_hints: list[str] = []
        expected_type = _infer_expected_type_for_output_names(fn_def, output_names, type_hints)

        slot_spec = _make_slot_spec(
            filename=filename,
            func_qualname=func_qualname,
            start_abs_lineno=start_abs,
            end_abs_lineno=end_abs,
            spec_text=spec_text,
            free_variables=free_vars,
            control_context=control_context,
            expected_category=SlotCategory.STATEMENT_BLOCK,
            expected_type=expected_type,
            output_names=output_names,
            formal_constraints=formal_constraints,
            usage_hints=usage_hints,
            enclosing_function_source=source,
            enclosing_function_qualname=func_qualname,
        )
        comment_slots.append((start_abs, slot_spec))

        i = j

    slots: list[SlotSpec] = []
    slots.extend([s for _, s in sorted(comment_slots, key=lambda t: t[0])])
    slots.extend([s for _, s in sorted(semi_slots, key=lambda t: t[0])])
    slots.sort(key=lambda s: s.source_span[1])

    if not slots:
        # Whole function body becomes a single slot.
        # Use the whole body text as spec_text so the agent gets full context.
        spec_text = dedented
        start_abs = first_lineno + 1
        end_abs = first_lineno + len(source_lines)
        rel_start = 1
        bound_before = set(a.arg for a in fn_def.args.args)
        bound_before |= set(a.arg for a in fn_def.args.posonlyargs)
        bound_before |= set(a.arg for a in fn_def.args.kwonlyargs)
        if fn_def.args.vararg:
            bound_before.add(fn_def.args.vararg.arg)
        if fn_def.args.kwarg:
            bound_before.add(fn_def.args.kwarg.arg)
        control_context = _control_context_for_line(fn_def, rel_start, func_qualname)
        expected_type = type_hints.get("return") if type_hints and "return" in type_hints else type(None)
        # For FUNCTION_BODY we use a special output_names contract: empty = return value only.
        slots = [
            _make_slot_spec(
                filename=filename,
                func_qualname=func_qualname,
                start_abs_lineno=start_abs,
                end_abs_lineno=end_abs,
                spec_text=spec_text,
                free_variables=_ordered_vars_from_fn(fn_def, bound_before),
                control_context=control_context,
                expected_category=SlotCategory.FUNCTION_BODY,
                expected_type=expected_type,
                output_names=[],
                formal_constraints=[],
                usage_hints=[],
                enclosing_function_source=source,
                enclosing_function_qualname=func_qualname,
            )
        ]
    return slots


def lower_to_scaffold(
    source: str,
    slot_specs: list[SlotSpec],
    slot_index_offset: int = 0,
) -> str:
    """
    Produce valid Python scaffold by replacing open regions:
    - STATEMENT_BLOCK (#> block):
        - replace the comment region with __slot_N__(...) assignment(s)
    - EXPRESSION (semi() call):
        - replace the call expression with __slot_N__(...)

    Returns scaffold source string (must ast.parse cleanly).
    """
    dedented = textwrap.dedent(source)
    try:
        tree = ast.parse(dedented)
    except SyntaxError:
        return dedented

    if not tree.body or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return dedented
    fn_def = tree.body[0]
    # Avoid recursively re-applying @semiformal during scaffold compilation.
    fn_def.decorator_list = []

    # If no open regions (#> blocks and semi() calls) were found, we must still route
    # execution through the slot resolver by wrapping the whole function body.
    fn_body_slots = [s for s in slot_specs if s.expected_category == SlotCategory.FUNCTION_BODY]
    if len(slot_specs) == 1 and len(fn_body_slots) == 1:
        spec = fn_body_slots[0]
        call_idx = (slot_specs.index(spec) + slot_index_offset)
        keywords = [
            ast.keyword(arg=name, value=ast.Name(id=name, ctx=ast.Load()))
            for name in spec.free_variables
        ]
        call_expr = ast.Call(
            func=ast.Name(id=f"__slot_{call_idx}__", ctx=ast.Load()),
            args=[],
            keywords=keywords,
        )
        fn_def.body = [ast.Return(value=call_expr)]
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)

    # Detect comment blocks in dedented source so we can align absolute line numbers.
    comment_blocks: list[tuple[int, int]] = []  # (rel_start, rel_end) 1-based, inclusive
    lines = dedented.splitlines()
    i = 0
    while i < len(lines):
        if not _is_hash_arrow(lines[i]):
            i += 1
            continue
        start = i
        j = i
        while j < len(lines) and _is_hash_arrow(lines[j]):
            j += 1
        end = j - 1
        # rel_* are 1-based in the dedented source for AST lineno comparisons.
        comment_blocks.append((start + 1, end + 1))
        i = j

    stmt_slots = [(idx, s) for idx, s in enumerate(slot_specs) if s.expected_category == SlotCategory.STATEMENT_BLOCK]
    stmt_slots.sort(key=lambda t: t[1].source_span[1])
    expr_slots = [(idx, s) for idx, s in enumerate(slot_specs) if s.expected_category == SlotCategory.EXPRESSION]
    expr_slots.sort(key=lambda t: t[1].source_span[1])

    # Align dedented first line absolute number using the first statement block (if any).
    dedented_first_abs: Optional[int] = None
    if stmt_slots and comment_blocks:
        first_stmt_abs_start = stmt_slots[0][1].source_span[1]
        first_comment_rel_start = comment_blocks[0][0]
        dedented_first_abs = first_stmt_abs_start - (first_comment_rel_start - 1)

    # --- Rewrite expression semi(...) calls by node order ---
    # Collect all semi() call nodes with their precise location.
    semi_call_nodes: list[tuple[int, int, ast.Call]] = []

    class _SemiCollector(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "semi":
                semi_call_nodes.append((getattr(node, "lineno", 0) or 0, getattr(node, "col_offset", 0) or 0, node))
            self.generic_visit(node)

    _SemiCollector().visit(fn_def)
    semi_call_nodes.sort(key=lambda t: (t[0], t[1]))

    semi_map: dict[tuple[int, int], int] = {}
    if len(semi_call_nodes) == len(expr_slots):
        for i_expr, (_, _, _node) in enumerate(semi_call_nodes):
            overall_idx, _spec = expr_slots[i_expr]
            key = (semi_call_nodes[i_expr][0], semi_call_nodes[i_expr][1])
            semi_map[key] = overall_idx

    class _SemiRewriter(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            node = self.generic_visit(node)
            if not isinstance(node, ast.Call):
                return node
            if isinstance(node.func, ast.Name) and node.func.id == "semi":
                node_key = (getattr(node, "lineno", 0) or 0, getattr(node, "col_offset", 0) or 0)
                overall_idx = semi_map.get(node_key)
                if overall_idx is None:
                    return node
                spec = slot_specs[overall_idx]
                call_idx = overall_idx + slot_index_offset
                kwargs = [
                    ast.keyword(arg=name, value=ast.Name(id=name, ctx=ast.Load()))
                    for name in spec.free_variables
                ]
                return ast.Call(func=ast.Name(id=f"__slot_{call_idx}__", ctx=ast.Load()), args=[], keywords=kwargs)
            return node

    tree = _SemiRewriter().visit(tree)
    ast.fix_missing_locations(tree)

    # --- Insert statement block slots as assignments ---
    # Insert before the first AST statement after each #> block end.
    insertions: list[tuple[int, list[ast.stmt]]] = []
    for block_index, (overall_idx, spec) in enumerate(stmt_slots):
        # Map the statement spec to its comment block by index in source order.
        if not comment_blocks:
            break
        if block_index >= len(comment_blocks):
            break
        _rel_start, rel_end = comment_blocks[block_index]
        # rel_end is inclusive comment end; insert before first stmt whose lineno > rel_end.
        insert_at = len(fn_def.body)
        for s_i, stmt in enumerate(fn_def.body):
            stmt_lineno = getattr(stmt, "lineno", 0) or 0
            if stmt_lineno > rel_end:
                insert_at = s_i
                break

        call_idx = overall_idx + slot_index_offset
        call_expr = ast.Call(
            func=ast.Name(id=f"__slot_{call_idx}__", ctx=ast.Load()),
            args=[],
            keywords=[ast.keyword(arg=name, value=ast.Name(id=name, ctx=ast.Load())) for name in spec.free_variables],
        )

        if not spec.output_names:
            new_stmts: list[ast.stmt] = [ast.Expr(value=call_expr)]
        elif len(spec.output_names) == 1:
            new_stmts = [
                ast.Assign(
                    targets=[ast.Name(id=spec.output_names[0], ctx=ast.Store())],
                    value=call_expr,
                )
            ]
        else:
            tmp = f"_r_{call_idx}"
            new_stmts = [
                ast.Assign(targets=[ast.Name(id=tmp, ctx=ast.Store())], value=call_expr),
            ]
            for out_name in spec.output_names:
                key = ast.Constant(value=out_name)
                sub = ast.Subscript(
                    value=ast.Name(id=tmp, ctx=ast.Load()),
                    slice=key,
                    ctx=ast.Load(),
                )
                new_stmts.append(
                    ast.Assign(
                        targets=[ast.Name(id=out_name, ctx=ast.Store())],
                        value=sub,
                    )
                )

        insertions.append((insert_at, new_stmts))

    insertions.sort(key=lambda t: t[0], reverse=True)
    for insert_at, new_stmts in insertions:
        fn_def.body[insert_at:insert_at] = new_stmts

    ast.fix_missing_locations(tree)
    return ast.unparse(tree)

