"""Rich-based console output for agent steps, validation errors, and confirmations."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from semipy.types import (
    CacheEntry,
    GenerationSpec,
    GenerationStrategy,
    SemiCallSite,
    ValidationResult,
)

_console: Optional[Console] = None


def get_console() -> Console:
    """Return the shared Rich Console (works in terminal, REPL, and Jupyter)."""
    global _console
    if _console is None:
        _console = Console()
    return _console


def strategy_description(strategy: GenerationStrategy) -> str:
    """Human-readable explanation of the generation strategy."""
    if strategy == GenerationStrategy.FRESH:
        return "Generate new code (no cached match)"
    if strategy == GenerationStrategy.REUSE:
        return "Use cached implementation if available"
    if strategy == GenerationStrategy.INCREMENTAL:
        return "Incremental update from existing code"
    return str(strategy.value)


def _format_location(filename: str, lineno: int, func_qualname: str) -> str:
    """Short location string: file:line function."""
    from os.path import basename
    f = basename(filename) if filename else "<unknown>"
    fn = func_qualname or "?"
    return f"{f}:{lineno} {fn}"


def _call_site_file_url(filename: str, lineno: int) -> str:
    """file:// URL for opening at line (IDE-friendly)."""
    if not filename:
        return ""
    p = Path(filename).resolve()
    try:
        uri = p.as_uri()
        return f"{uri}#L{lineno}" if lineno else uri
    except Exception:
        return ""


def _format_cache_path(cache_dir: Optional[Path], entry: CacheEntry) -> str:
    """Display path for cached implementation (session entry module or generic)."""
    if entry.cache_display_path:
        return entry.cache_display_path
    if cache_dir is not None:
        return str(cache_dir.resolve())
    return ".semiformal/runtime"


def _short_display_path(full_path: str) -> str:
    """Short path for one-line display: .../site_id/hash.py so it fits and stays one line."""
    try:
        p = Path(full_path).resolve()
        parts = p.parts
        if "runtime" in parts:
            i = parts.index("runtime")
            if i + 2 <= len(parts):
                return ".../" + "/".join(parts[i + 1 : i + 3])
        return p.name if len(p.name) <= 52 else f"...{p.name[-48:]}"
    except Exception:
        return full_path if len(full_path) <= 52 else f"...{full_path[-48:]}"


def _file_link_url(path: str) -> str:
    """file:// URL for terminal hyperlink (Rich [link=...])."""
    try:
        return Path(path).resolve().as_uri()
    except Exception:
        return ""


# Dedupe: same (loc, outcome, path) only printed once per process.
_semipy_log_printed: set[tuple[str, str, str]] = set()


def _print_semipy_line_once(
    loc: str,
    outcome: str,
    path_or_msg: str,
    style: str = "green",
    loc_link: Optional[str] = None,
    path_link: Optional[str] = None,
) -> None:
    """One line: [semipy] location -> outcome -> path/msg; location and path clickable when links given."""
    key = (loc, outcome, path_or_msg)
    if key in _semipy_log_printed:
        return
    _semipy_log_printed.add(key)
    console = get_console()
    loc_part = f"[link={loc_link}]{loc}[/link]" if loc_link else loc
    path_display = _short_display_path(path_or_msg) if (path_link and path_or_msg.strip()) else path_or_msg
    path_part = f"[link={path_link}]{path_display}[/link]" if path_link else path_display
    console.print(
        f"[dim][semipy][/] {loc_part} [{style}]-> {outcome} ->[/] {path_part}",
        no_wrap=True,
    )


def print_cache_hit_from_semi(
    spec: GenerationSpec,
    entry: CacheEntry,
    cache_dir: Optional[Path] = None,
) -> None:
    """One line: user code location -> cache hit -> path (clickable); once per unique call site + path."""
    cs = spec.call_site
    loc = _format_location(cs.filename, cs.lineno, cs.func_qualname or "")
    path = _format_cache_path(cache_dir, entry)
    _print_semipy_line_once(
        loc, "cache hit", path, "green",
        loc_link=_call_site_file_url(cs.filename, cs.lineno),
        path_link=_file_link_url(path),
    )


def cache_hit_panel(
    spec: GenerationSpec,
    entry: CacheEntry,
    cache_dir: Optional[Path] = None,
) -> None:
    """Print a panel for cache hit: call site, file path, and syntax-highlighted source skeleton."""
    console = get_console()
    cs = spec.call_site
    type_name = getattr(entry.expected_type, "__name__", str(entry.expected_type))
    if type_name == "NoneType":
        type_name = "any"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="cyan", justify="right")
    grid.add_column(style="white")
    grid.add_row("File:", cs.filename)
    grid.add_row("Line:", str(cs.lineno))
    grid.add_row("Function:", cs.func_qualname or "(top-level)")
    grid.add_row("Return type:", type_name)
    if entry.cache_display_path:
        grid.add_row("Cache file:", entry.cache_display_path)

    body = grid
    src = entry.generated_source.strip()
    lines = src.splitlines()
    max_lines = 25
    if len(lines) > max_lines:
        src = "\n".join(lines[:max_lines]) + "\n  ..."
    syntax = Syntax(src, "python", theme="monokai", line_numbers=True)
    console.print(Panel(body, title="Using cached implementation", border_style="green"))
    console.print(Panel(syntax, title="Cached source", border_style="dim"))


def validation_error_panel(result: ValidationResult, source_preview: Optional[str] = None) -> None:
    """Print a panel with validation failure details."""
    console = get_console()
    lines = []
    if not result.ast_valid:
        lines.append("[red]AST:[/] invalid (parse/syntax error)")
    if not result.type_correct:
        lines.append("[red]Type:[/] return type mismatch")
    if not result.execution_ok:
        lines.append("[red]Execution:[/] test run failed")
    if result.error_message:
        lines.append("[red]Error:[/] " + result.error_message)
    body = "\n".join(lines) if lines else "Validation failed (no details)"
    if source_preview:
        body += "\n\n[dim]Source preview:[/]\n" + source_preview
    console.print(Panel(body, title="Validation failed", border_style="red"))


def source_preview(source: str, max_lines: int = 15) -> str:
    """Return a truncated source snippet for display."""
    lines = source.strip().splitlines()
    if len(lines) <= max_lines:
        return source.strip()
    return "\n".join(lines[:max_lines]) + "\n  ..."


def confirm(
    prompt_text: str,
    default_no: bool = True,
    confirm_callback: Optional[Callable[[str], str]] = None,
) -> bool:
    """
    Ask the user for confirmation. Uses confirm_callback if provided (e.g. for Jupyter widgets),
    otherwise Console.input(). Returns True for yes, False for no.
    """
    console = get_console()
    suffix = " [y/N]" if default_no else " [Y/n]"
    full_prompt = prompt_text + suffix + " "
    if confirm_callback is not None:
        raw = confirm_callback(full_prompt).strip().lower()
    else:
        raw = console.input(full_prompt).strip().lower()
    if not raw:
        return not default_no
    return raw in ("y", "yes")


# --- Progress: single status line + one summary at end (no stacking) ---

class GenerationProgress:
    """Updates a single status line and prints one summary when the run finishes."""

    def __init__(self, verbose: bool) -> None:
        self._verbose = verbose
        self._result: Optional[str] = None
        self._cache_hit: Optional[tuple[GenerationSpec, CacheEntry]] = None
        self._cache_dir: Optional[Path] = None
        self._success_attempt: Optional[int] = None
        self._success_display_path: Optional[str] = None
        self._success_call_site: Optional[SemiCallSite] = None
        self._failure_msg: Optional[str] = None
        self._failure_call_site: Optional[SemiCallSite] = None
        self._failure_validation: Optional[ValidationResult] = None
        self._failure_source: Optional[str] = None
        self._steps: list[str] = []
        self._status = None

    def update(self, message: str) -> None:
        if not self._verbose:
            return
        console = get_console()
        if self._status is not None:
            self._status.update(f"[bold blue]{message}[/]")
        else:
            self._status = console.status(f"[bold blue]{message}[/]")
            self._status.__enter__()

    def log_step(self, step: str) -> None:
        """Record a decision step for the final summary."""
        if self._verbose:
            self._steps.append(step)

    def _stop_status(self) -> None:
        if self._status is not None:
            try:
                self._status.__exit__(None, None, None)
            except Exception:
                pass
            self._status = None

    def record_cache_hit(
        self,
        spec: GenerationSpec,
        entry: CacheEntry,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self._result = "cache_hit"
        self._cache_hit = (spec, entry)
        if cache_dir is not None:
            self._cache_dir = cache_dir

    def record_success(
        self,
        attempt: int = 1,
        cache_dir: Optional[Path] = None,
        call_site: Optional[SemiCallSite] = None,
        display_path: Optional[str] = None,
    ) -> None:
        self._result = "success"
        self._success_attempt = attempt
        if cache_dir is not None:
            self._cache_dir = cache_dir
        self._success_display_path = display_path
        self._success_call_site = call_site

    def record_failure(
        self,
        message: str,
        validation_result: Optional[ValidationResult] = None,
        source: Optional[str] = None,
        call_site: Optional[SemiCallSite] = None,
    ) -> None:
        self._result = "failure"
        self._failure_msg = message
        self._failure_validation = validation_result
        self._failure_source = source
        self._failure_call_site = call_site

    def print_summary(self) -> None:
        import sys
        self._stop_status()
        if not self._verbose or self._result is None:
            return
        console = get_console()
        if self._result == "cache_hit" and self._cache_hit is not None:
            spec, entry = self._cache_hit
            cs = spec.call_site
            loc = _format_location(cs.filename, cs.lineno, cs.func_qualname or "")
            path = _format_cache_path(self._cache_dir, entry)
            _print_semipy_line_once(
                loc, "cache hit", path, "green",
                loc_link=_call_site_file_url(cs.filename, cs.lineno),
                path_link=_file_link_url(path),
            )
        elif self._result == "success":
            loc = ""
            loc_link = None
            if self._success_call_site is not None:
                cs = self._success_call_site
                loc = _format_location(cs.filename, cs.lineno, cs.func_qualname or "")
                loc_link = _call_site_file_url(cs.filename, cs.lineno)
            path = self._success_display_path or ""
            path_link = _file_link_url(path) if path else None
            _print_semipy_line_once(
                loc, "generated", path, "green",
                loc_link=loc_link,
                path_link=path_link,
            )
        elif self._result == "failure":
            loc = ""
            loc_link = None
            if self._failure_call_site is not None:
                cs = self._failure_call_site
                loc = _format_location(cs.filename, cs.lineno, cs.func_qualname or "")
                loc_link = _call_site_file_url(cs.filename, cs.lineno)
            msg = self._failure_msg or "validation failed"
            _print_semipy_line_once(loc, "failed", msg, "red", loc_link=loc_link)
            if self._failure_validation is not None and self._failure_source is not None:
                validation_error_panel(
                    self._failure_validation,
                    source_preview(self._failure_source),
                )
        if getattr(console, "file", None) is not None:
            try:
                console.file.flush()
            except Exception:
                pass
        else:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass


@contextmanager
def generation_progress(verbose: bool):
    """
    Context manager for one generate() run. Yields a GenerationProgress that updates
    a single status line; on exit prints one summary (cache hit panel, success line, or failure).
    Use this to avoid stacking many step lines.
    """
    progress = GenerationProgress(verbose)
    try:
        yield progress
    finally:
        progress.print_summary()
