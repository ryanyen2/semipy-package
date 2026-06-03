"""
Pure formatting helpers for the semiformal pipeline console output.

All functions here take primitive arguments and return strings (or similar
values) with no side effects and no shared state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from semipy.types import CacheEntry, Decision


def decision_description(decision: Decision) -> str:
    """Human-readable explanation of the resolution decision."""
    if decision == Decision.REUSE:
        return "Reuse cached implementation"
    if decision == Decision.ADAPT:
        return "Adapt from previous implementation"
    if decision == Decision.GENERATE:
        return "Generate new implementation"
    if decision == Decision.INSTANTIATE:
        return "Instantiate from learned pattern"
    return str(decision.value)


def pipeline_resolution_message(decision: Decision) -> str:
    """Short user-facing line after resolve (why generation runs)."""
    if decision == Decision.REUSE:
        return "Using a matching cached implementation."
    if decision == Decision.ADAPT:
        return "No exact reuse; adapting from a previous version and generating code."
    if decision == Decision.GENERATE:
        return "No reusable implementation; creating a new one."
    if decision == Decision.INSTANTIATE:
        return "Matching a learned pattern; substituting parameters without generation."
    return decision_description(decision)


def pipeline_generate_status(attempt: int, total: int, *, retry: bool) -> str:
    """Status line while the agent runs (replaces technical 'Calling agent')."""
    if retry:
        return f"Adjusting implementation after validation (attempt {attempt}/{total})…"
    return f"Implementing code (attempt {attempt}/{total})…"


def _format_location(filename: str, lineno: int, func_qualname: str) -> str:
    """Short location string: file:line (function)."""
    from os.path import basename
    f = basename(filename) if filename else "<unknown>"
    fn = func_qualname or "?"
    return f"{f}:{lineno} ({fn})"


def _call_site_file_url(filename: str, lineno: int) -> str:
    """file:// URL for opening at line (IDE-friendly). Resolved path; if not under cwd (e.g. Jupyter temp), use absolute URI."""
    if not filename:
        return ""
    try:
        p = Path(filename).resolve()
        uri = p.as_uri()
        return f"{uri}:{lineno}" if lineno else uri
    except Exception:
        return ""


def _relative_path_for_display(path: str, line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Path relative to cwd for log display (e.g. examples/use_csv_kit.py or examples/csv_kit/table.py:69)."""
    return _relative_display_path(path, line, end_line, max_len=72)


def _format_cache_path(cache_dir: Optional[Path], entry: CacheEntry) -> str:
    """Display path for cached implementation (session entry module or generic)."""
    if entry.cache_display_path:
        return entry.cache_display_path
    if cache_dir is not None:
        return str(cache_dir.resolve())
    return ".semiformal/runtime"


def _relative_display_path(
    path: str,
    line: Optional[int] = None,
    end_line: Optional[int] = None,
    max_len: int = 56,
) -> str:
    """Path relative to cwd for log display; keeps under max_len."""
    try:
        p = Path(path).resolve()
        try:
            s = str(p.relative_to(Path.cwd()))
        except ValueError:
            s = p.name
        if line is not None:
            s = f"{s}:{line}" if end_line is None or end_line == line else f"{s}:{line}-{end_line}"
        if len(s) <= max_len:
            return s
        if ":" in s:
            path_part, line_part = s.split(":", 1)
            budget = max_len - len(line_part) - 1
            path_part = ("..." + path_part[-budget + 3 :]) if len(path_part) > budget else path_part
            return f"{path_part}:{line_part}"
        return "..." + s[-(max_len - 3) :]
    except Exception:
        return (path[: max_len - 3] + "...") if len(path) > max_len else path


def _file_link_url(path: str) -> str:
    """file:// URL for terminal hyperlink (Rich [link=...])."""
    try:
        return Path(path).resolve().as_uri()
    except Exception:
        return ""


def _path_with_line_range(path: str, line_range: tuple[int, int]) -> str:
    """Return path:start-end for IDE link when line_range is non-zero (absolute file:// URI)."""
    if not path or line_range == (0, 0):
        return path
    s, e = line_range
    if s <= 0:
        return path
    try:
        uri = Path(path).resolve().as_uri()
        return f"{uri}:{s}-{e}" if e > s else f"{uri}:{s}"
    except Exception:
        return path


def _relative_path_with_line_range(path: str, line_range: tuple[int, int]) -> str:
    """Return relative path:start-end for command-click link (e.g. .semiformal/runtime/table.semi.py:42-97)."""
    if not path or line_range == (0, 0):
        return path
    s, e = line_range
    if s <= 0:
        return _relative_display_path(path, max_len=72)
    try:
        p = Path(path).resolve()
        try:
            rel = str(p.relative_to(Path.cwd()))
        except ValueError:
            rel = p.name
        return f"{rel}:{s}-{e}" if e > s else f"{rel}:{s}"
    except Exception:
        return path


def _traceback_style_location(path: str, line: int, end_line: Optional[int] = None) -> str:
    """Format as Python traceback so VSCode/terminal makes it command-clickable: File \"path\", line N."""
    try:
        abs_path = str(Path(path).resolve())
    except Exception:
        abs_path = path
    if end_line is not None and end_line != line:
        return f'File "{abs_path}":{line}-{end_line}'
    return f'File "{abs_path}":{line}'


def _format_call_source(call_site: object) -> str:
    """Human-readable call site: relative path and line (function)."""
    loc = _relative_display_path(call_site.filename, call_site.lineno, max_len=72)  # type: ignore[attr-defined]
    fn = call_site.func_qualname or "?"  # type: ignore[attr-defined]
    return f"{loc} ({fn})"


def _format_call_site_short(call_site: object) -> str:
    """Short call site for progress line: file:line (func)."""
    from os.path import basename
    f = basename(call_site.filename) if call_site.filename else "<unknown>"  # type: ignore[attr-defined]
    fn = call_site.func_qualname or "?"  # type: ignore[attr-defined]
    return f"{f}:{call_site.lineno} ({fn})"  # type: ignore[attr-defined]
