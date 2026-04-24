"""Program-analysis derived guardrails for slot generation prompts.

The semiformal implementation is constrained by the surrounding formal code:
  * static parameter type annotations on the enclosing function,
  * downstream attribute and key accesses on each output name,
  * control-flow context (enclosing conditionals / loops / preconditions),
  * logic-flow successors (calls the outputs are passed into, with the
    callee's declared parameter types when available).

This module uses AST-only analysis (no regex / keyword matching, no call into
the user's runtime) to distill those constraints into a compact, budgeted
text block. The generation prompt prepends the block so the LLM treats the
formal code as hard context rather than rediscovering it from raw source.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Optional

from semipy.types import SlotCategory, SlotSpec


_DEFAULT_BUDGET_CHARS = 2000


@dataclass
class ProgramAnalysisResult:
    """Summary of program analysis for one slot."""

    parameter_types: dict[str, str] = field(default_factory=dict)
    output_attribute_accesses: dict[str, list[str]] = field(default_factory=dict)
    output_key_accesses: dict[str, list[str]] = field(default_factory=dict)
    output_passed_to: list[tuple[str, str, str]] = field(default_factory=list)
    control_flow_context: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    return_annotation: str = ""

    def is_empty(self) -> bool:
        return not (
            self.parameter_types
            or self.output_attribute_accesses
            or self.output_key_accesses
            or self.output_passed_to
            or self.control_flow_context
            or self.preconditions
            or self.return_annotation
        )


def _ann_to_source(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _find_enclosing_function(tree: ast.AST, qualname: str) -> Optional[ast.AST]:
    """Locate the FunctionDef / AsyncFunctionDef whose name matches the leaf of ``qualname``.

    ``qualname`` may be ``ClassName.method`` or just ``func_name``. We match the
    leaf segment only to stay robust against nested qualifications; if multiple
    candidates exist we return the first in source order (deterministic).
    """
    leaf = qualname.split(".")[-1] if qualname else ""
    if not leaf:
        return None
    matches: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == leaf:
                matches.append(node)
    if not matches:
        return None
    return matches[0]


def _collect_parameter_types(
    func: ast.AST,
    free_variables: list[str],
) -> dict[str, str]:
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return {}
    out: dict[str, str] = {}
    for arg in getattr(func.args, "args", []):
        if arg.arg == "self":
            continue
        if free_variables and arg.arg not in free_variables:
            continue
        ann = _ann_to_source(arg.annotation)
        if ann:
            out[arg.arg] = ann
    # Also capture kwonly args when they appear in free_variables.
    for arg in getattr(func.args, "kwonlyargs", []):
        if free_variables and arg.arg not in free_variables:
            continue
        ann = _ann_to_source(arg.annotation)
        if ann:
            out[arg.arg] = ann
    return out


def _line_in_range(node: ast.AST, lo: int, hi: int) -> bool:
    start = getattr(node, "lineno", 0)
    if not start:
        return False
    # A node is "in" a range if it starts strictly after the slot anchor line.
    return start >= lo and start <= hi


def _collect_post_slot_usage(
    tree: ast.AST,
    output_names: list[str],
    slot_end_line: int,
    func_end_line: int,
) -> tuple[
    dict[str, list[str]],
    dict[str, list[str]],
    list[tuple[str, str, str]],
]:
    """For each output name, record downstream attribute/key accesses and call uses.

    The scan is bounded to (slot_end_line, func_end_line] so we see only
    what happens *after* the slot produces a value, not earlier assignments.
    """
    attr_map: dict[str, list[str]] = {}
    key_map: dict[str, list[str]] = {}
    call_uses: list[tuple[str, str, str]] = []

    if not output_names:
        return attr_map, key_map, call_uses

    name_set = set(output_names)

    for node in ast.walk(tree):
        if not _line_in_range(node, slot_end_line + 1, func_end_line):
            continue
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in name_set:
                attr_map.setdefault(node.value.id, [])
                if node.attr not in attr_map[node.value.id]:
                    attr_map[node.value.id].append(node.attr)
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id in name_set:
                key = _extract_constant_key(node.slice)
                if key is None:
                    continue
                key_map.setdefault(node.value.id, [])
                if key not in key_map[node.value.id]:
                    key_map[node.value.id].append(key)
        elif isinstance(node, ast.Call):
            target = _call_display_name(node.func)
            if not target:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in name_set:
                    call_uses.append((arg.id, target, "positional"))
                elif isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name) and arg.value.id in name_set:
                    call_uses.append((arg.value.id, target, "starred"))
            for kw in node.keywords:
                if isinstance(kw.value, ast.Name) and kw.value.id in name_set and kw.arg:
                    call_uses.append((kw.value.id, target, f"kw:{kw.arg}"))
    return attr_map, key_map, call_uses


def _extract_constant_key(slice_node: ast.AST) -> Optional[str]:
    # Python 3.9+: subscript slice is the expression itself, not an Index wrapper.
    if isinstance(slice_node, ast.Constant):
        if isinstance(slice_node.value, (str, int)):
            return repr(slice_node.value)
    return None


def _call_display_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_display_name(node.value)
        if base:
            return f"{base}.{node.attr}"
        return node.attr
    return ""


def _collect_control_flow_context(
    tree: ast.AST,
    slot_start_line: int,
    slot_end_line: int,
    output_names: list[str],
) -> tuple[list[str], list[str]]:
    """Return (enclosing control descriptions, precondition test snippets)."""
    enclosing: list[str] = []
    preconditions: list[str] = []

    def walk(node: ast.AST, path: list[str]) -> None:
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start)
        if not start:
            return
        if not (start <= slot_start_line and slot_end_line <= end):
            return
        desc = _describe_control_node(node)
        if desc and desc not in path:
            path = path + [desc]
            enclosing.append(desc)
        for child in ast.iter_child_nodes(node):
            walk(child, path)

    walk(tree, [])

    # Preconditions: any `if <expr>:` after the slot whose test references an output name.
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            start = getattr(node, "lineno", 0)
            if start and start > slot_end_line:
                _capture_precondition_tests(node.test, output_names, preconditions)
    # Deduplicate.
    dedup: list[str] = []
    for p in preconditions:
        if p not in dedup:
            dedup.append(p)
    return enclosing, dedup


def _describe_control_node(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return f"function `{node.name}`"
    if isinstance(node, ast.ClassDef):
        return f"class `{node.name}`"
    if isinstance(node, ast.For):
        tgt = _ann_to_source(node.target)
        it = _ann_to_source(node.iter)
        return f"for {tgt} in {it}" if tgt or it else "for-loop"
    if isinstance(node, ast.While):
        return "while-loop"
    if isinstance(node, ast.If):
        test = _ann_to_source(node.test)
        return f"if {test}" if test else "if-branch"
    if isinstance(node, ast.With):
        return "with-block"
    if isinstance(node, ast.Try):
        return "try-block"
    return ""


def _capture_precondition_tests(
    test_expr: ast.AST,
    output_names: list[str],
    out: list[str],
) -> None:
    name_set = set(output_names)
    for n in ast.walk(test_expr):
        if isinstance(n, ast.Name) and n.id in name_set:
            snippet = _ann_to_source(test_expr)
            if snippet:
                out.append(snippet)
            return
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in name_set:
            snippet = _ann_to_source(test_expr)
            if snippet:
                out.append(snippet)
            return
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id in name_set:
            snippet = _ann_to_source(test_expr)
            if snippet:
                out.append(snippet)
            return


def _resolve_callee_signature(
    tree: ast.AST,
    display_name: str,
) -> Optional[tuple[list[str], list[str], str]]:
    """Best-effort: if display_name resolves to a FunctionDef in the same module,
    return (positional_arg_names, positional_arg_annotations, return_annotation).
    """
    leaf = display_name.split(".")[-1]
    if not leaf:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == leaf:
            arg_names = [a.arg for a in node.args.args if a.arg != "self"]
            arg_anns = [_ann_to_source(a.annotation) for a in node.args.args if a.arg != "self"]
            ret = _ann_to_source(node.returns)
            return arg_names, arg_anns, ret
    return None


def _return_annotation_for(
    func: ast.AST,
) -> str:
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    return _ann_to_source(func.returns)


def analyze_slot(
    slot_spec: SlotSpec,
    user_source_code: str | None,
) -> ProgramAnalysisResult:
    """AST-analyze ``user_source_code`` in the neighbourhood of ``slot_spec``.

    Returns an empty :class:`ProgramAnalysisResult` on parse failure so the
    generation prompt can silently fall back to the unannotated behaviour.
    """
    empty = ProgramAnalysisResult()
    if not user_source_code or not user_source_code.strip():
        return empty
    try:
        tree = ast.parse(user_source_code)
    except SyntaxError:
        return empty

    qualname = slot_spec.enclosing_function_qualname or ""
    func = _find_enclosing_function(tree, qualname)

    parameter_types: dict[str, str] = {}
    return_ann = ""
    if func is not None:
        parameter_types = _collect_parameter_types(func, list(slot_spec.free_variables or []))
        return_ann = _return_annotation_for(func)

    _, slot_start, slot_end = slot_spec.source_span
    # enclosing_function_span uses absolute lines; when the analysis tree is
    # the whole user file we can use those directly. Otherwise we fall back
    # to scanning the whole module.
    func_end = slot_end
    if func is not None:
        func_end = getattr(func, "end_lineno", slot_end) or slot_end

    output_names = list(slot_spec.output_names or [])
    attr_map, key_map, call_uses = _collect_post_slot_usage(
        tree, output_names, slot_end, func_end,
    )

    # Enrich call_uses with callee signatures where resolvable.
    enriched_calls: list[tuple[str, str, str]] = []
    for out_name, callee, kind in call_uses:
        sig = _resolve_callee_signature(tree, callee)
        if sig is None:
            enriched_calls.append((out_name, callee, kind))
            continue
        arg_names, arg_anns, ret = sig
        if kind == "positional" and arg_names and arg_anns and arg_anns[0]:
            enriched_calls.append((
                out_name,
                f"{callee}({arg_names[0]}: {arg_anns[0]}) -> {ret or 'Any'}",
                kind,
            ))
        elif kind.startswith("kw:"):
            kw_name = kind.split(":", 1)[1]
            if kw_name in arg_names:
                idx = arg_names.index(kw_name)
                ann = arg_anns[idx] if idx < len(arg_anns) else ""
                if ann:
                    enriched_calls.append((
                        out_name,
                        f"{callee}({kw_name}: {ann}) -> {ret or 'Any'}",
                        kind,
                    ))
                    continue
            enriched_calls.append((out_name, callee, kind))
        else:
            enriched_calls.append((out_name, callee, kind))

    enclosing, preconditions = _collect_control_flow_context(
        tree, slot_start, slot_end, output_names,
    )

    return ProgramAnalysisResult(
        parameter_types=parameter_types,
        output_attribute_accesses=attr_map,
        output_key_accesses=key_map,
        output_passed_to=enriched_calls,
        control_flow_context=enclosing,
        preconditions=preconditions,
        return_annotation=return_ann,
    )


def render_analysis_block(
    result: ProgramAnalysisResult,
    *,
    budget_chars: int = _DEFAULT_BUDGET_CHARS,
    slot_category: SlotCategory | None = None,
) -> str:
    """Render the analysis as a compact markdown block bounded by ``budget_chars``.

    Lines are appended in priority order; once the budget is exhausted the
    remainder is dropped (not truncated mid-line). Returns an empty string
    when the analysis carries no actionable information.
    """
    if result.is_empty():
        return ""

    out: list[str] = ["## Formal code constraints (derived from surrounding program)"]

    if result.parameter_types:
        out.append("")
        out.append("Input parameter types (from the enclosing function annotations):")
        for name in sorted(result.parameter_types):
            out.append(f"  - {name}: {result.parameter_types[name]}")

    if result.return_annotation and slot_category is not None and slot_category in (
        SlotCategory.FUNCTION_BODY,
        SlotCategory.STATEMENT_BLOCK,
    ):
        out.append("")
        out.append(
            f"Enclosing function return annotation: `{result.return_annotation}` "
            "(your implementation must produce values compatible with this type)."
        )

    if result.output_key_accesses:
        out.append("")
        out.append("Required output keys (downstream code indexes into the output with these keys):")
        for name in sorted(result.output_key_accesses):
            keys = result.output_key_accesses[name]
            out.append(f"  - {name}[...]: {', '.join(keys)}")

    if result.output_attribute_accesses:
        out.append("")
        out.append("Required output attributes (downstream code reads these attributes):")
        for name in sorted(result.output_attribute_accesses):
            attrs = result.output_attribute_accesses[name]
            out.append(f"  - {name}.*: {', '.join(attrs)}")

    if result.output_passed_to:
        out.append("")
        out.append("Output logic-flow (downstream callers consuming each output):")
        seen: set[tuple[str, str, str]] = set()
        for out_name, callee, kind in result.output_passed_to:
            triple = (out_name, callee, kind)
            if triple in seen:
                continue
            seen.add(triple)
            out.append(f"  - {out_name} -> {callee} ({kind})")

    if result.preconditions:
        out.append("")
        out.append("Downstream preconditions checked on the output:")
        for p in result.preconditions:
            out.append(f"  - {p}")

    if result.control_flow_context:
        out.append("")
        out.append("Enclosing control-flow context (outermost -> innermost):")
        for d in result.control_flow_context:
            out.append(f"  - {d}")

    block = "\n".join(out)
    if len(block) <= budget_chars:
        return block

    # Greedy line-wise trimming so we never cut mid-line.
    trimmed: list[str] = []
    total = 0
    header = out[0]
    trimmed.append(header)
    total += len(header) + 1
    for line in out[1:]:
        if total + len(line) + 1 > budget_chars:
            break
        trimmed.append(line)
        total += len(line) + 1
    return "\n".join(trimmed)


def build_program_analysis_context(
    slot_spec: SlotSpec | None,
    user_source_code: str | None,
    *,
    budget_chars: int = _DEFAULT_BUDGET_CHARS,
) -> str:
    """Top-level entry point used by the agent prompt builder.

    Returns an empty string when there is no slot_spec or the analysis finds
    no actionable constraint; the caller should only inject the block when
    non-empty to avoid diluting the prompt.
    """
    if slot_spec is None:
        return ""
    if not user_source_code:
        return ""
    try:
        result = analyze_slot(slot_spec, user_source_code)
    except Exception:
        return ""
    return render_analysis_block(
        result,
        budget_chars=budget_chars,
        slot_category=slot_spec.expected_category,
    )
