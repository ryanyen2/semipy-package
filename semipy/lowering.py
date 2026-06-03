from __future__ import annotations

import ast
import textwrap
from dataclasses import replace
from typing import Any

from semipy.types import (
    SlotCategory,
    SlotSpec,
    _sha16,
    _stable_slot_hash,
    compute_spec_equivalence_key,
)
from semipy.lowering_ast import (
    INLINE_ASSIGN,
    INLINE_IF_TEST,
    INLINE_EXPR,
    INLINE_RETURN,
    _is_hash_arrow,
    _collect_hash_arrow_block_ranges,
    _inline_hash_arrow_spec,
    _is_ellipsis_constant,
    _ellipsis_assign_targets,
    _find_stmt_at_rel_line,
    _replace_if_test_at_line,
    _replace_expr_value_at_line,
    _replace_return_value_at_line,
    _splice_assign_replacement,
    _strip_hash_arrow,
    _relative_to_abs_lineno,
    _abs_to_rel_lineno,
    _assigned_names,
    _loaded_names,
    _function_def_from_source,
    _ordered_vars_from_fn,
    _bound_names_before,
    _infer_expected_type_for_output_names,
    _control_context_for_line,
    _extract_formal_constraint_lines,
    _infer_output_names_for_statement_block,
    strip_skeleton_lines,  # noqa: F401  (re-exported for decorator.py / skeleton_writer.py)
    _BUILTIN_NAMES,
)


def _slot_id_interim(
    filename: str,
    func_qualname: str,
    start_abs_lineno: int,
    end_abs_lineno: int,
    spec_text: str,
) -> str:
    """Unique id before ordinal assignment; not used for portal identity after finalize."""
    key = f"{filename}:{func_qualname}:{start_abs_lineno}:{end_abs_lineno}:{spec_text}"
    return _stable_slot_hash(key)


def _make_slot_id(filename: str, func_qualname: str, slot_ordinal: int, spec_text: str) -> str:
    """
    Stable slot identity for a region inside one enclosing function.

    Keyed on ``(filename, func_qualname, spec_text)`` so inserting, removing, or
    reordering *other* slots in the same function does not remint this slot's id.
    The ordinal argument is retained in the signature for backward compatibility
    but is intentionally not mixed into the key — ordinal drift was the source
    of phantom 0-commit duplicates when a new ``#>`` block was added above an
    existing one.
    """
    del slot_ordinal
    key = f"{filename}:{func_qualname}:{spec_text}"
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
    enclosing_function_span: tuple[str, int, int] = ("", 0, 0),
) -> SlotSpec:
    spec_hash = _sha16(spec_text)
    spec_equivalence_key = compute_spec_equivalence_key(
        spec_text,
        free_variables,
        expected_type,
        expected_category=expected_category,
        output_names=output_names,
    )
    slot_id = _slot_id_interim(filename, func_qualname, start_abs_lineno, end_abs_lineno, spec_text)
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
        enclosing_function_span=enclosing_function_span,
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
    - #> comment blocks -> STATEMENT_BLOCK slots
    - semi(...) Call nodes -> EXPRESSION slots
    If neither is found: return single FUNCTION_BODY slot for the whole body.
    """
    dedented = textwrap.dedent(source)
    source_lines = dedented.splitlines()
    try:
        ast.parse(dedented)  # validate syntax; result unused
    except SyntaxError:
        # Let the decorator compilation decide what to do; returning empty keeps errors localized.
        return []
    fn_def = _function_def_from_source(dedented)
    if fn_def is None:
        return []

    fn_end_rel = getattr(fn_def, "end_lineno", None) or fn_def.lineno
    func_end_abs = _relative_to_abs_lineno(first_lineno, fn_end_rel)
    enclosing_span = (filename, first_lineno, func_end_abs)

    exclude_names: set[str] = set()
    if globals_ns:
        exclude_names |= set(globals_ns.keys())
    # Builtins shouldn't be treated as "outputs" of a #> block.
    try:
        import builtins as _builtins
        exclude_names |= set(dir(_builtins))
    except Exception:
        pass

    # The method receiver (``self``/``cls``) is not slot data: it is the instance,
    # not an input the spec consumes. Excluding it keeps it out of the generated
    # signature, the runtime data profile, and the reuse fingerprint. (If a slot
    # needs instance state, the spec interpolates the attribute value, not ``self``.)
    receiver_names: set[str] = set()
    _params = [a.arg for a in (fn_def.args.posonlyargs + fn_def.args.args)]
    if "." in func_qualname and _params and _params[0] in ("self", "cls"):
        receiver_names.add(_params[0])

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
            free_var_set = loaded_in_prompt - {"semi"} - _BUILTIN_NAMES - receiver_names
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
                enclosing_function_span=enclosing_span,
            )
            semi_slots.append((start_abs, slot_spec))

    # Collect #> comment blocks for SlotSpec creation (STATEMENT_BLOCK).
    comment_slots: list[tuple[int, SlotSpec]] = []
    for block_start_idx, block_end_idx in _collect_hash_arrow_block_ranges(source_lines):
        indent = source_lines[block_start_idx][: len(source_lines[block_start_idx]) - len(source_lines[block_start_idx].lstrip())]
        block_lines = [source_lines[k] for k in range(block_start_idx, block_end_idx + 1) if _is_hash_arrow(source_lines[k])]
        spec_text = "\n".join(_strip_hash_arrow(line) for line in block_lines).strip()

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
        free_var_set = bound_before - assigned_in_region - set(output_names) - _BUILTIN_NAMES - receiver_names
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
            enclosing_function_span=enclosing_span,
        )
        comment_slots.append((start_abs, slot_spec))

    # End-of-line #> (e.g. ``name = ... #> spec``) is a Python comment; collect as STATEMENT_BLOCK.
    inline_slots: list[tuple[int, SlotSpec]] = []
    for line_idx, line in enumerate(source_lines):
        spec_tail = _inline_hash_arrow_spec(line)
        if spec_tail is None:
            continue
        rel_line = line_idx + 1
        stmt = _find_stmt_at_rel_line(fn_def, rel_line)
        if stmt is None:
            continue
        hint: str | None = None
        output_names: list[str] = []
        expected_type: Any = type(None)
        if isinstance(stmt, ast.Assign) and _ellipsis_assign_targets(stmt):
            hint = INLINE_ASSIGN
            output_names = _ellipsis_assign_targets(stmt)
            expected_type = _infer_expected_type_for_output_names(fn_def, output_names, type_hints)
        elif isinstance(stmt, ast.If) and _is_ellipsis_constant(stmt.test):
            hint = INLINE_IF_TEST
            expected_type = bool
        elif isinstance(stmt, ast.Expr) and _is_ellipsis_constant(stmt.value):
            hint = INLINE_EXPR
            expected_type = type(None)
        elif isinstance(stmt, ast.Return) and _is_ellipsis_constant(stmt.value):
            hint = INLINE_RETURN
            expected_type = (
                type_hints.get("return") if type_hints and "return" in type_hints else type(None)
            )
        if hint is None:
            continue

        block_end_rel = rel_line
        indent = source_lines[line_idx][: len(source_lines[line_idx]) - len(source_lines[line_idx].lstrip())]
        bound_before = _bound_names_before(fn_def, block_end_rel + 1)

        assigned_in_region: set[str] = set()
        for st in fn_def.body:
            st_ln = getattr(st, "lineno", 0) or 0
            if st_ln > block_end_rel:
                if st_ln > block_end_rel + 20:
                    break
                assigned_in_region |= _assigned_names(st)
        free_var_set = bound_before - assigned_in_region - set(output_names) - _BUILTIN_NAMES - receiver_names
        free_vars = _ordered_vars_from_fn(fn_def, free_var_set)

        control_context = _control_context_for_line(fn_def, block_end_rel, func_qualname)
        formal_constraints = _extract_formal_constraint_lines(source_lines, indent, line_idx)

        start_abs = first_lineno + line_idx
        end_abs = start_abs
        usage_hints = [hint]
        slot_spec = _make_slot_spec(
            filename=filename,
            func_qualname=func_qualname,
            start_abs_lineno=start_abs,
            end_abs_lineno=end_abs,
            spec_text=spec_tail,
            free_variables=free_vars,
            control_context=control_context,
            expected_category=SlotCategory.STATEMENT_BLOCK,
            expected_type=expected_type,
            output_names=output_names,
            formal_constraints=formal_constraints,
            usage_hints=usage_hints,
            enclosing_function_source=source,
            enclosing_function_qualname=func_qualname,
            enclosing_function_span=enclosing_span,
        )
        inline_slots.append((start_abs, slot_spec))

    slots: list[SlotSpec] = []
    merged_comment = sorted(comment_slots + inline_slots, key=lambda t: t[0])
    slots.extend([s for _, s in merged_comment])
    slots.extend([s for _, s in sorted(semi_slots, key=lambda t: t[0])])
    slots.sort(key=lambda s: s.source_span[1])
    slots = [
        replace(s, slot_id=_make_slot_id(filename, func_qualname, i, s.spec_text))
        for i, s in enumerate(slots)
    ]

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
        fb = _make_slot_spec(
            filename=filename,
            func_qualname=func_qualname,
            start_abs_lineno=start_abs,
            end_abs_lineno=end_abs,
            spec_text=spec_text,
            free_variables=_ordered_vars_from_fn(fn_def, bound_before - receiver_names),
            control_context=control_context,
            expected_category=SlotCategory.FUNCTION_BODY,
            expected_type=expected_type,
            output_names=[],
            formal_constraints=[],
            usage_hints=[],
            enclosing_function_source=source,
            enclosing_function_qualname=func_qualname,
            enclosing_function_span=enclosing_span,
        )
        slots = [replace(fb, slot_id=_make_slot_id(filename, func_qualname, 0, fb.spec_text))]
    return slots


def lower_to_scaffold(
    source: str,
    slot_specs: list[SlotSpec],
    slot_index_offset: int = 0,
    *,
    dedent_anchor_abs: int | None = None,
) -> str:
    """
    Produce valid Python scaffold by replacing open regions:
    - STATEMENT_BLOCK (#> block):
        - replace the comment region with __slot_N__(...) assignment(s)
    - STATEMENT_BLOCK (end-of-line #> on ``...`` placeholders):
        - replace the Ellipsis assignment / if-test / expr / return value
    - EXPRESSION (semi() call):
        - replace the call expression with __slot_N__(...)

    ``dedent_anchor_abs`` is the absolute line number of the first line of ``textwrap.dedent``
    source (usually ``inspect.getsourcelines`` first line, e.g. ``@semiformal``).

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

    lines = dedented.splitlines()
    comment_blocks: list[tuple[int, int]] = [
        (s + 1, e + 1) for s, e in _collect_hash_arrow_block_ranges(lines)
    ]  # (rel_start, rel_end) 1-based, inclusive

    stmt_slots = [(idx, s) for idx, s in enumerate(slot_specs) if s.expected_category == SlotCategory.STATEMENT_BLOCK]
    stmt_slots.sort(key=lambda t: t[1].source_span[1])
    expr_slots = [(idx, s) for idx, s in enumerate(slot_specs) if s.expected_category == SlotCategory.EXPRESSION]
    expr_slots.sort(key=lambda t: t[1].source_span[1])

    anchor = dedent_anchor_abs
    if anchor is None and stmt_slots and comment_blocks:
        first_stmt_abs_start = stmt_slots[0][1].source_span[1]
        first_comment_rel_start = comment_blocks[0][0]
        anchor = first_stmt_abs_start - (first_comment_rel_start - 1)
    if anchor is None and stmt_slots:
        anchor = stmt_slots[0][1].source_span[1] - (fn_def.lineno - 1)

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

    # --- Statement slots: assignments / inserts, then inline if/expr/return ---
    original_body = list(fn_def.body)
    insertions: list[tuple[int, list[ast.stmt]]] = []
    rel_replacements: list[tuple[int, int, list[ast.stmt]]] = []
    deferred_control: list[tuple[int, SlotSpec]] = []

    for block_index, (overall_idx, spec) in enumerate(stmt_slots):
        hints = spec.usage_hints
        if INLINE_IF_TEST in hints or INLINE_EXPR in hints or INLINE_RETURN in hints:
            deferred_control.append((overall_idx, spec))
            continue

        if anchor is not None:
            end_rel = spec.source_span[2] - anchor + 1
            start_rel = spec.source_span[1] - anchor + 1
        else:
            if not comment_blocks or block_index >= len(comment_blocks):
                break
            _bs, end_rel = comment_blocks[block_index]
            start_rel = comment_blocks[block_index][0]

        call_idx = overall_idx + slot_index_offset
        call_expr = ast.Call(
            func=ast.Name(id=f"__slot_{call_idx}__", ctx=ast.Load()),
            args=[],
            keywords=[ast.keyword(arg=name, value=ast.Name(id=name, ctx=ast.Load())) for name in spec.free_variables],
        )

        if not spec.output_names:
            new_stmts = [ast.Expr(value=call_expr)]
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

        n_out = len(spec.output_names)
        inline_assign = INLINE_ASSIGN in hints

        if n_out > 0 and inline_assign:
            insert_at_inline: int | None = None
            for s_i, stmt in enumerate(original_body):
                if getattr(stmt, "lineno", 0) == start_rel:
                    insert_at_inline = s_i
                    break
            match_ok = insert_at_inline is not None
            if match_ok and insert_at_inline is not None:
                for k in range(n_out):
                    si = insert_at_inline + k
                    if si >= len(original_body):
                        match_ok = False
                        break
                    st = original_body[si]
                    if not isinstance(st, ast.Assign) or len(st.targets) != 1:
                        match_ok = False
                        break
                    if not isinstance(st.targets[0], ast.Name) or st.targets[0].id != spec.output_names[k]:
                        match_ok = False
                        break
                    v = st.value
                    if not isinstance(v, ast.Constant) or v.value is not Ellipsis:
                        match_ok = False
                        break
            if match_ok:
                rel_replacements.append((start_rel, n_out, new_stmts))
                continue

        insert_at = len(original_body)
        for s_i, stmt in enumerate(original_body):
            stmt_lineno = getattr(stmt, "lineno", 0) or 0
            if stmt_lineno > end_rel:
                insert_at = s_i
                break

        if n_out > 0 and insert_at < len(original_body):
            match_ok = True
            for k in range(n_out):
                si = insert_at + k
                if si >= len(original_body):
                    match_ok = False
                    break
                st = original_body[si]
                if not isinstance(st, ast.Assign) or len(st.targets) != 1:
                    match_ok = False
                    break
                if not isinstance(st.targets[0], ast.Name) or st.targets[0].id != spec.output_names[k]:
                    match_ok = False
                    break
                v = st.value
                if not isinstance(v, ast.Constant) or v.value is not Ellipsis:
                    match_ok = False
                    break
            if match_ok:
                rel_replacements.append(
                    (getattr(original_body[insert_at], "lineno", 0) or 0, n_out, new_stmts)
                )
                continue

        insertions.append((insert_at, new_stmts))

    rel_replacements.sort(key=lambda t: t[0], reverse=True)
    for start_rel, count, new_stmts in rel_replacements:
        _splice_assign_replacement(fn_def.body, start_rel, count, new_stmts)

    def _insert_offset(orig_pos: int) -> int:
        delta = 0
        for r_start, r_count, r_stmts in rel_replacements:
            if r_start + r_count <= orig_pos:
                delta += len(r_stmts) - r_count
        return orig_pos + delta

    insertions.sort(key=lambda t: t[0], reverse=True)
    for insert_at, new_stmts in insertions:
        at = _insert_offset(insert_at)
        fn_def.body[at:at] = new_stmts

    if anchor is None and deferred_control and stmt_slots:
        anchor = stmt_slots[0][1].source_span[1] - (fn_def.lineno - 1)

    if anchor is not None:
        deferred_control.sort(key=lambda t: t[1].source_span[1])
        for overall_idx, spec in deferred_control:
            rel_line = spec.source_span[1] - anchor + 1
            call_idx = overall_idx + slot_index_offset
            call_expr = ast.Call(
                func=ast.Name(id=f"__slot_{call_idx}__", ctx=ast.Load()),
                args=[],
                keywords=[
                    ast.keyword(arg=name, value=ast.Name(id=name, ctx=ast.Load()))
                    for name in spec.free_variables
                ],
            )
            if INLINE_IF_TEST in spec.usage_hints:
                _replace_if_test_at_line(fn_def, rel_line, call_expr)
            elif INLINE_EXPR in spec.usage_hints:
                _replace_expr_value_at_line(fn_def, rel_line, call_expr)
            elif INLINE_RETURN in spec.usage_hints:
                _replace_return_value_at_line(fn_def, rel_line, call_expr)

    ast.fix_missing_locations(tree)
    return ast.unparse(tree)
