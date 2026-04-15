"""
Write `#<` reasoning skeleton lines into the user's source file after GENERATE/ADAPT.

`#>` lines are user spec (never modified). `#<` lines are system-managed: stripped on each
new generation and replaced with a fresh structured skeleton derived deterministically from
the CommitmentRecord produced during generation.

Skeleton lines are inserted at the top of the function body using the CommitmentRecord fields:
  [Task]   <- goal
  [Given]  <- givens (up to 2)
  [Then]   <- decision_points (up to 2)
  [When]   <- assumptions (up to 1)
  [Verify] <- checks_performed (up to 1)
  [But]    <- rejected_alternatives (up to 1)

**When a file is updated**

- Only ``@semiformal`` slots carry ``enclosing_function_source``; the writer replaces the
  enclosing function in that ``.py`` file. **Standalone** ``semi(...)`` outside any decorated
  function has no enclosing function body to attach to, so nothing is written.
- The skeleton pass runs only when resolution is **GENERATE** or **ADAPT**. On **REUSE** the
  pipeline does not call the writer, so existing ``#<`` lines are left as-is.
"""
from __future__ import annotations

import ast
import textwrap
import threading
import traceback
from pathlib import Path
from typing import Any

from semipy.agents.console_io import print_pipeline_log
from semipy.lowering import strip_skeleton_lines
from semipy.models import CommitmentRecord
from semipy.types import CacheEntry, SlotSpec, SemiCallSite

_file_write_locks: dict[str, threading.Lock] = {}
_file_write_locks_mutex = threading.Lock()


def _log_surface(slot_spec: SlotSpec, message: str) -> None:
    call_site = SemiCallSite(
        filename=slot_spec.source_span[0],
        lineno=slot_spec.source_span[1],
        func_qualname=slot_spec.enclosing_function_qualname,
    )
    print_pipeline_log(call_site, "surface", message)


def _lock_for_path(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _file_write_locks_mutex:
        if key not in _file_write_locks:
            _file_write_locks[key] = threading.Lock()
        return _file_write_locks[key]


def _body_start_index(lines: list[str]) -> int:
    """Return the index of the first body line (after def + optional docstring)."""
    def_idx: int | None = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            def_idx = i
            break
    if def_idx is None:
        return 0
    # Skip optional docstring
    insert_after = def_idx
    if def_idx + 1 < len(lines):
        next_stripped = lines[def_idx + 1].lstrip()
        if next_stripped.startswith('"""') or next_stripped.startswith("'''"):
            quote = '"""' if next_stripped.startswith('"""') else "'''"
            content_after_open = next_stripped[3:]
            if quote in content_after_open:
                insert_after = def_idx + 1
            else:
                for j in range(def_idx + 2, len(lines)):
                    if quote in lines[j]:
                        insert_after = j
                        break
    return insert_after + 1  # first body line index


def _first_return_index(lines: list[str], body_start: int) -> int:
    """Return index of first 'return' statement line, or last meaningful line."""
    for i in range(body_start, len(lines)):
        stripped = lines[i].lstrip()
        if stripped.startswith("return ") or stripped == "return":
            return i
    # Fallback: last non-empty line
    for i in range(len(lines) - 1, body_start - 1, -1):
        if lines[i].strip():
            return i
    return len(lines) - 1


def _insert_annotations_inline(
    fn_source: str,
    annotations: list[Any],
    indent: str,
) -> str:
    """Place #< annotation lines inline in fn_source near their anchor code.

    Each annotation has:
      tag    — e.g. Task, Given, Then, Verify
      text   — concise annotation text
      anchor — code substring: insert BEFORE the first body line containing it.
               Empty string → insert at function body start (grouped before any code).
               "RETURN"    → insert just before the first return statement.

    Multiple anchors that resolve to the same line are inserted in the order they
    appear in the annotations list (top-to-bottom before that line).
    Anchors not found fall back to just before the first return statement.
    """
    if not annotations:
        return fn_source

    lines = fn_source.splitlines(keepends=True)
    body_start = _body_start_index(lines)
    return_idx = _first_return_index(lines, body_start)

    def annotation_line(note: Any) -> str:
        tag = (getattr(note, "tag", "") or "").strip()
        text = (getattr(note, "text", "") or "").strip()
        return f"{indent}#< [{tag}] {text}\n"

    # Resolve each annotation to an insertion point (line index to insert BEFORE).
    # Insertions: list of (resolved_line_idx, original_order, formatted_line)
    insertions: list[tuple[int, int, str]] = []
    for order, note in enumerate(annotations):
        anchor = (getattr(note, "anchor", "") or "").strip()
        fmt = annotation_line(note)
        if not anchor:
            target = body_start
        elif anchor.upper() == "RETURN":
            target = return_idx
        else:
            target = None
            for i in range(body_start, len(lines)):
                stripped = lines[i].lstrip()
                # Skip existing #< lines so we don't anchor off old annotations
                if stripped.startswith("#<"):
                    continue
                if anchor in lines[i]:
                    target = i
                    break
            if target is None:
                target = return_idx  # fallback
        insertions.append((target, order, fmt))

    # Sort by (line_idx ASC, order ASC) then apply in REVERSE so indices stay valid
    insertions.sort(key=lambda x: (x[0], x[1]))

    result = list(lines)
    # Apply in reverse to preserve forward indices
    for target, _order, fmt in reversed(insertions):
        result.insert(target, fmt)
    return "".join(result)


def _drop_empty_hash_only_lines(source: str) -> str:
    """Remove lines that are only ``#`` (placeholders from prior strips); keep ``#<`` / ``#>``."""
    out: list[str] = []
    for line in source.splitlines(keepends=True):
        core = line.rstrip("\r\n")
        stripped = core.lstrip()
        if stripped.startswith("#<") or stripped.startswith("#>") or stripped.startswith("# >"):
            out.append(line)
            continue
        if stripped.startswith("#"):
            remainder = stripped[1:].lstrip()
            if remainder == "":
                continue
        out.append(line)
    return "".join(out)


def _function_simple_name(func_qualname: str) -> str:
    return func_qualname.split(".")[-1]


def _extract_function_source_for_name(parsed_source: str, simple_name: str) -> str | None:
    try:
        tree = ast.parse(parsed_source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == simple_name:
            seg = ast.get_source_segment(parsed_source, node)
            if isinstance(seg, str) and seg.strip():
                return seg
    return None


def _strip_leading_decorators(source: str) -> str:
    """Remove leading @decorator lines from source so they are not duplicated.

    Used as a post-processing step on the fallback function source (from
    ``fn_source_clean_all.lstrip()``) when ``_extract_function_source_for_name``
    fails.  The on-disk decorator is preserved via ``lines[:start-1]`` in the
    replacement, so including it in ``sk_lines`` would create a double decorator.
    """
    lines = source.lstrip().splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped and not stripped.startswith("@"):
            return "".join(lines[i:])
    return source.lstrip()


def _find_function_span_in_file(
    file_text: str,
    func_qualname: str,
    hint_start: int,
) -> tuple[int, int]:
    if hint_start <= 0:
        return (0, 0)
    try:
        tree = ast.parse(file_text)
    except SyntaxError:
        return (0, 0)
    simple = _function_simple_name(func_qualname)
    candidates: list[tuple[int, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == simple:
            # Use the def line (not the decorator line) as start. This means
            # lines[:start-1] retains the on-disk decorator in the prefix, and
            # sk_lines (produced by ast.get_source_segment which starts at the def
            # line) does not duplicate it. The fallback path strips decorator lines
            # from fn_source_clean so they do not appear in sk_lines at all.
            start = node.lineno
            end = getattr(node, "end_lineno", None) or node.lineno
            dist = abs(start - hint_start)
            if dist < 30:
                candidates.append((start, end, dist))
    if candidates:
        candidates.sort(key=lambda t: t[2])
        return (candidates[0][0], candidates[0][1])
    fallback: list[tuple[int, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == simple:
            start = node.lineno
            end = getattr(node, "end_lineno", None) or node.lineno
            fallback.append((start, end, abs(start - hint_start)))
    if not fallback:
        return (0, 0)
    fallback.sort(key=lambda t: t[2])
    return (fallback[0][0], fallback[0][1])


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".semipy_skeleton.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _split_line_core_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _reindent_to_match(skeleton_fn: str, file_lines: list[str], func_start_line: int) -> str:
    """Canonicalize skeleton indentation, then align with the on-disk ``def`` line."""
    if not skeleton_fn.strip():
        return skeleton_fn
    original_line = file_lines[func_start_line - 1] if func_start_line >= 1 else ""
    target_cols = len(original_line) - len(original_line.lstrip())

    lines = skeleton_fn.splitlines(keepends=True)
    def_idx: int | None = None
    d_def = 0
    for i, line in enumerate(lines):
        core, _ = _split_line_core_ending(line)
        st = core.lstrip()
        if st.startswith("def ") or st.startswith("async def "):
            def_idx = i
            d_def = len(core) - len(core.lstrip())
            break
    if def_idx is None:
        return skeleton_fn

    # Step 1: subtract d_def so def sits at column 0
    worked: list[str] = []
    for line in lines:
        core, ending = _split_line_core_ending(line)
        if not core.strip():
            worked.append(line)
            continue
        cur = len(core) - len(core.lstrip())
        if cur >= d_def:
            worked.append(" " * (cur - d_def) + core.lstrip() + ending)
        else:
            worked.append(line)

    # Step 2: normalize minimum body indent to 4 spaces
    indents: list[int] = []
    for i in range(def_idx + 1, len(worked)):
        core, _ = _split_line_core_ending(worked[i])
        if not core.strip():
            continue
        indents.append(len(core) - len(core.lstrip()))
    if indents:
        min_body = min(indents)
        if min_body > 4:
            reduction = min_body - 4
            adjusted: list[str] = []
            for i, line in enumerate(worked):
                core, ending = _split_line_core_ending(line)
                if i <= def_idx or not core.strip():
                    adjusted.append(line)
                    continue
                cur = len(core) - len(core.lstrip())
                if cur >= min_body:
                    adjusted.append(" " * (cur - reduction) + core.lstrip() + ending)
                else:
                    adjusted.append(line)
            worked = adjusted

    # Step 3: prepend file indent
    out: list[str] = []
    for line in worked:
        core, ending = _split_line_core_ending(line)
        if not core.strip():
            out.append(line)
            continue
        out.append(" " * target_cols + core + ending)
    return "".join(out)


def _body_indent(fn_source: str) -> str:
    """Infer the body indent string from the first non-empty line after the def line."""
    lines = fn_source.splitlines()
    found_def = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            found_def = True
            continue
        if found_def and stripped:
            n_spaces = len(line) - len(line.lstrip())
            return " " * n_spaces
    return "    "


def surface_skeleton(
    slot_spec: SlotSpec,
    cache_entry: CacheEntry,
) -> None:
    """Replace the enclosing function in the on-disk file with a version that includes `#<` lines.

    Derives #< lines deterministically from cache_entry.commitment_record.
    Skips Jupyter ephemeral kernel paths and standalone semi() slots with no enclosing function.
    Not invoked on REUSE.
    """
    try:
        _surface_skeleton_impl(slot_spec, cache_entry)
    except Exception:
        traceback.print_exc()


def _surface_skeleton_impl(slot_spec: SlotSpec, cache_entry: CacheEntry) -> None:
    if not slot_spec.enclosing_function_source.strip():
        return

    target = Path(slot_spec.source_span[0])
    try:
        resolved = str(target.resolve())
    except OSError:
        return
    if "ipykernel" in resolved.replace("\\", "/").lower():
        return
    if not target.is_file() or target.suffix.lower() != ".py":
        return

    record: CommitmentRecord | None = getattr(cache_entry, "commitment_record", None)
    if not isinstance(record, CommitmentRecord):
        _log_surface(slot_spec, "Skeleton skipped: no CommitmentRecord in cache entry.")
        return

    fn_source_clean_all = strip_skeleton_lines(slot_spec.enclosing_function_source)
    simple_qual = slot_spec.enclosing_function_qualname
    simple_name = _function_simple_name(simple_qual)
    # Dedent before parsing so that class methods (which carry file-level indent)
    # can be parsed by ast.parse at module level without IndentationError.
    fn_source_clean_dedented = textwrap.dedent(fn_source_clean_all)
    fn_source_clean = _extract_function_source_for_name(fn_source_clean_dedented, simple_name)
    if fn_source_clean is None:
        # Fallback: strip leading decorator lines so they are not duplicated by
        # the on-disk prefix (lines[:start-1] already contains the decorator).
        fn_source_clean = _strip_leading_decorators(fn_source_clean_dedented)

    # Determine body indent from the function source
    indent = _body_indent(fn_source_clean)

    # Build inline annotations from CommitmentRecord
    annotations = list(getattr(record, "annotations", None) or [])
    if not annotations:
        _log_surface(slot_spec, "Skeleton skipped: CommitmentRecord had no annotations.")
        return

    # Insert annotation #< lines inline near their anchor code
    annotated_fn = _insert_annotations_inline(fn_source_clean, annotations, indent)
    annotated_fn = _drop_empty_hash_only_lines(annotated_fn)

    hint_start = slot_spec.enclosing_function_span[1] or slot_spec.source_span[1]

    lock = _lock_for_path(target)
    with lock:
        file_text = target.read_text(encoding="utf-8")
        start, end = _find_function_span_in_file(file_text, simple_qual, hint_start)
        if start == 0 or end == 0:
            return

        lines = file_text.splitlines(keepends=True)
        annotated_fn = _reindent_to_match(annotated_fn, lines, start)

        sk_lines = annotated_fn.splitlines(keepends=True)
        if sk_lines and not sk_lines[-1].endswith("\n"):
            sk_lines[-1] += "\n"

        new_text = "".join(lines[: start - 1] + sk_lines + lines[end:])
        _atomic_write_text(target, new_text)
        _log_surface(slot_spec, "Skeleton surfaced to source file.")
