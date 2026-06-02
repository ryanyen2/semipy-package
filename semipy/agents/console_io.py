"""
Rich-based console output for the semiformal pipeline.

Provides one-line DAG logs (reuse/adapt/generate), validation error panels,
progress status, and confirmation prompts. In Jupyter, agent generation output
is captured and printed as one scrollable Panel per cell.
"""
from __future__ import annotations

import atexit
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.markdown import Markdown

from semipy.types import (
    CacheEntry,
    GenerationSpec,
    SemiCallSite,
    ValidationResult,
)

from semipy.agents.console_core import (
    _is_jupyter,  # noqa: F401
    get_console,
    jupyter_capture_console,  # noqa: F401
)
from semipy.agents.console_format import (
    _format_location,
    _call_site_file_url,
    _relative_path_for_display,
    _format_cache_path,
    _relative_display_path,
    _file_link_url,
    _relative_path_with_line_range,
    _format_call_source,
    _format_call_site_short,
    decision_description,  # noqa: F401
    pipeline_generate_status,  # noqa: F401
    pipeline_resolution_message,  # noqa: F401
)

# Dedupe: same (call_file, source, generation, path) only printed once per process.
_semipy_log_printed: set[tuple[str, str, str, str]] = set()

# Batched one-line logs for high-frequency paths (e.g. REUSE verify on every row).
_PIPELINE_AGG_STAGES = frozenset({"reuse", "reuse_verify"})
_pipeline_agg_pending: Optional[dict[str, Any]] = None
_ipython_agg_flush_registered = False


def _pipeline_agg_key(call_site: SemiCallSite, stage: str, message: str) -> tuple[Any, ...]:
    """Group by stage + message + file + function; ignore lineno so multiple #> lines merge."""
    try:
        path = str(Path(call_site.filename).resolve())
    except Exception:
        path = call_site.filename or ""
    return (stage, message, path, call_site.func_qualname or "")


def _format_call_site_aggregate(
    filename: str,
    lineno_lo: int,
    lineno_hi: int,
    func_qualname: str,
) -> str:
    """Like _format_call_site_short but collapses a lineno range (e.g. 160-163)."""
    from os.path import basename

    f = basename(filename) if filename else "<unknown>"
    fn = func_qualname or "?"
    if lineno_lo == lineno_hi:
        line_s = str(lineno_lo)
    else:
        line_s = f"{lineno_lo}-{lineno_hi}"
    return f"{f}:{line_s} ({fn})"


def _ensure_ipython_post_cell_flush() -> None:
    """Flush batched pipeline logs after each notebook cell (reuse rows often fill a cell alone)."""
    global _ipython_agg_flush_registered
    if _ipython_agg_flush_registered:
        return
    try:
        from IPython import get_ipython

        ip = get_ipython()
        if ip is None:
            return

        def _flush_after_cell(*_a: Any, **_k: Any) -> None:
            flush_pipeline_log_pending()

        ev = getattr(ip, "events", None)
        if ev is not None:
            try:
                ev.register("post_run_cell", _flush_after_cell)
                _ipython_agg_flush_registered = True
                return
            except Exception:
                pass
        reg = getattr(ip, "register_post_execute", None)
        if callable(reg):
            try:
                reg(_flush_after_cell)
                _ipython_agg_flush_registered = True
            except Exception:
                pass
    except Exception:
        pass


def flush_pipeline_log_pending() -> None:
    """Emit any batched pipeline log lines (reuse / reuse_verify). Idempotent; safe after each cell or script."""
    global _pipeline_agg_pending
    p = _pipeline_agg_pending
    if p is None:
        return
    _pipeline_agg_pending = None

    console = get_console()
    count: int = p["count"]
    stage: str = p["stage"]
    message: str = p["message"]
    first: SemiCallSite = p["first_call_site"]
    lo: int = p["min_lineno"]
    hi: int = p["max_lineno"]

    if count <= 1:
        loc = _format_call_site_short(first)
    else:
        loc = _format_call_site_aggregate(
            first.filename,
            lo,
            hi,
            first.func_qualname or "",
        )

    tail = f"[dim][semipy][/] [cyan][{stage}][/] {loc} {message}"
    if count > 1:
        col_w = max(7, len(str(count)) + 1)
        counter_cell = f"{count}x".rjust(col_w)
        line = f"[dim]{counter_cell}[/]  {tail}"
        console.print(line, crop=False, overflow="ignore", no_wrap=True)
        return

    console.print(tail, no_wrap=True, crop=False, overflow="ignore")


def _flush_pipeline_log_before_non_aggregate() -> None:
    flush_pipeline_log_pending()


atexit.register(flush_pipeline_log_pending)


def print_pipeline_log(
    call_site: Optional[SemiCallSite],
    stage: str,
    message: str,
) -> None:
    """Print one pipeline line: [semipy] [stage] call_site message (cohesive format).

    Stages ``reuse`` and ``reuse_verify`` batch consecutive identical messages that
    share the same source file and function; lineno ranges are collapsed (e.g. two
    ``#>`` blocks become ``file:160-163``) and a left column shows repeat count.
    """
    console = get_console()
    loc = _format_call_site_short(call_site) if call_site else "?"

    if call_site is not None and stage in _PIPELINE_AGG_STAGES:
        _ensure_ipython_post_cell_flush()
        key = _pipeline_agg_key(call_site, stage, message)
        global _pipeline_agg_pending
        if _pipeline_agg_pending is not None and _pipeline_agg_pending["key"] != key:
            flush_pipeline_log_pending()
        if _pipeline_agg_pending is None:
            _pipeline_agg_pending = {
                "key": key,
                "count": 1,
                "min_lineno": call_site.lineno,
                "max_lineno": call_site.lineno,
                "stage": stage,
                "message": message,
                "first_call_site": call_site,
            }
        else:
            _pipeline_agg_pending["count"] += 1
            _pipeline_agg_pending["min_lineno"] = min(
                _pipeline_agg_pending["min_lineno"],
                call_site.lineno,
            )
            _pipeline_agg_pending["max_lineno"] = max(
                _pipeline_agg_pending["max_lineno"],
                call_site.lineno,
            )
        return

    _flush_pipeline_log_before_non_aggregate()
    console.print(
        f"[dim][semipy][/] [cyan][{stage}][/] {loc} {message}",
        no_wrap=True,
    )


_LOG_MAX_WIDTH = 96


def _print_semipy_line_once(
    source: str,
    generation: str,
    code_path: str,
    style: str = "green",
    source_link: Optional[str] = None,
    path_link: Optional[str] = None,
    code_line_range: Optional[tuple[int, int]] = None,
    call_file: Optional[str] = None,
    call_file_link: Optional[str] = None,
) -> None:
    """Print [semipy] call file, package file, generation, transpiled file. All paths shown relative to cwd."""
    key = (call_file or "", source, generation, code_path, str(code_line_range) if code_line_range else "")
    if key in _semipy_log_printed:
        return
    _semipy_log_printed.add(key)
    console = get_console()
    # Call file (user script)
    call_part = ""
    if call_file:
        call_part = f"[link={call_file_link}]{call_file}[/link]" if call_file_link else call_file
        call_part = f"Call from {call_part}. "
    # Package file (where semi() is called)
    package_part = f"[link={source_link}]{source}[/link]" if source_link else source
    # Transpiled file (use relative path for link so command-click navigates)
    code_part = ""
    if code_path.strip():
        if code_line_range and code_line_range != (0, 0):
            s, e = code_line_range
            path_display = _relative_display_path(code_path, s, e, max_len=56)
            link = _relative_path_with_line_range(code_path, code_line_range)
        else:
            path_display = _relative_display_path(code_path, max_len=56)
            try:
                p = Path(code_path).resolve()
                link = str(p.relative_to(Path.cwd()))
            except ValueError:
                link = p.name
            except Exception:
                link = path_display
        code_part = f"[link={link}]{path_display}[/link]" if link else path_display
    line1 = f"[dim][semipy][/] {call_part}Package: {package_part}. [{style}]{generation}[/]"
    if code_part:
        line1 += f" Code at {code_part}."
    plain_len = len(f"[semipy] {call_part}Package: {source}. {generation}") + (len(f" Code at {code_part}.") if code_part else 0)
    try:
        width = console.width if getattr(console, "width", None) else _LOG_MAX_WIDTH
    except Exception:
        width = _LOG_MAX_WIDTH
    if plain_len > width and code_part:
        console.print(f"[dim][semipy][/] {call_part}Package: {package_part}. [{style}]{generation}[/].")
        console.print(f"  [dim]Code at[/] {code_part}.")
    else:
        console.print(line1, no_wrap=True)


def print_cache_hit_from_semi(
    spec: GenerationSpec,
    entry: CacheEntry,
    cache_dir: Optional[Path] = None,
    entry_script_path: Optional[str] = None,
    entry_script_lineno: Optional[int] = None,
) -> None:
    """One line: call file, package file, reused, code path. Paths relative to cwd."""
    cs = spec.call_site
    source = _relative_display_path(cs.filename, cs.lineno, max_len=72)
    if cs.func_qualname:
        source = f"{source} ({cs.func_qualname})"
    path = _format_cache_path(cache_dir, entry)
    call_file = None
    call_file_link = None
    if entry_script_path:
        call_file = _relative_path_for_display(entry_script_path, entry_script_lineno) if (entry_script_lineno is not None and entry_script_lineno > 0) else _relative_path_for_display(entry_script_path)
        call_file_link = _relative_link_path(entry_script_path, entry_script_lineno if (entry_script_lineno is not None and entry_script_lineno > 0) else None)
    _print_semipy_line_once(
        source, "Reused cached implementation.", path or "", "green",
        source_link=_call_site_file_url(cs.filename, cs.lineno),
        path_link=_file_link_url(path),
        call_file=call_file,
        call_file_link=call_file_link,
    )


def _relative_link_path(path: str, lineno: Optional[int] = None) -> str:
    """Relative path (and optional line) for command-click link."""
    try:
        p = Path(path).resolve()
        rel = str(p.relative_to(Path.cwd()))
    except ValueError:
        rel = p.name
    except Exception:
        return path
    if lineno is not None and lineno > 0:
        return f"{rel}:{lineno}"
    return rel


def print_dag_reuse(
    call_site: SemiCallSite,
    commit_id: str,
    code_path: str,
    source_link: Optional[str] = None,
    path_link: Optional[str] = None,
    code_line_range: Optional[tuple[int, int]] = None,
    entry_script_path: Optional[str] = None,
    entry_script_lineno: Optional[int] = None,
) -> None:
    """Log REUSE: call file, package file, reused implementation, transpiled path (optional line range). All paths relative."""
    source = _relative_display_path(call_site.filename, call_site.lineno, max_len=72)
    generation = f"Reused existing implementation (commit {commit_id[:8]})"
    call_file = None
    call_file_link = None
    if entry_script_path:
        call_file = _relative_path_for_display(entry_script_path, entry_script_lineno) if (entry_script_lineno is not None and entry_script_lineno > 0) else _relative_path_for_display(entry_script_path)
        call_file_link = _relative_link_path(entry_script_path, entry_script_lineno if (entry_script_lineno is not None and entry_script_lineno > 0) else None)
    _print_semipy_line_once(
        source, generation, code_path, "green", source_link, path_link, code_line_range,
        call_file=call_file,
        call_file_link=call_file_link,
    )


def print_dag_adapt(
    call_site: SemiCallSite,
    commit_id: str,
    parent_commit_id: str,
    code_path: str,
    source_link: Optional[str] = None,
    path_link: Optional[str] = None,
    code_line_range: Optional[tuple[int, int]] = None,
    entry_script_path: Optional[str] = None,
    entry_script_lineno: Optional[int] = None,
) -> None:
    """Log ADAPT: call file, package file, adapted from parent, transpiled path. All paths relative."""
    source = _relative_display_path(call_site.filename, call_site.lineno, max_len=72)
    generation = f"Adapted from previous (commit {parent_commit_id[:8]} -> {commit_id[:8]})"
    call_file = None
    call_file_link = None
    if entry_script_path:
        call_file = _relative_path_for_display(entry_script_path, entry_script_lineno) if (entry_script_lineno is not None and entry_script_lineno > 0) else _relative_path_for_display(entry_script_path)
        call_file_link = _relative_link_path(entry_script_path, entry_script_lineno if (entry_script_lineno is not None and entry_script_lineno > 0) else None)
    _print_semipy_line_once(
        source, generation, code_path, "cyan", source_link, path_link, code_line_range,
        call_file=call_file,
        call_file_link=call_file_link,
    )


def print_dag_compose(
    call_site: SemiCallSite,
    commit_id: str,
    code_path: str,
    source_link: Optional[str] = None,
    path_link: Optional[str] = None,
    code_line_range: Optional[tuple[int, int]] = None,
    entry_script_path: Optional[str] = None,
    entry_script_lineno: Optional[int] = None,
) -> None:
    """Log COMPOSE: composed from library primitive, new commit."""
    source = _relative_display_path(call_site.filename, call_site.lineno, max_len=72)
    generation = f"Compose from library (commit {commit_id[:8]})"
    call_file = None
    call_file_link = None
    if entry_script_path:
        call_file = _relative_path_for_display(entry_script_path, entry_script_lineno) if (entry_script_lineno is not None and entry_script_lineno > 0) else _relative_path_for_display(entry_script_path)
        call_file_link = _relative_link_path(entry_script_path, entry_script_lineno if (entry_script_lineno is not None and entry_script_lineno > 0) else None)
    _print_semipy_line_once(
        source, generation, code_path, "cyan", source_link, path_link, code_line_range,
        call_file=call_file,
        call_file_link=call_file_link,
    )


def print_dag_generate(
    call_site: SemiCallSite,
    commit_id: str,
    code_path: str,
    source_link: Optional[str] = None,
    path_link: Optional[str] = None,
    code_line_range: Optional[tuple[int, int]] = None,
    entry_script_path: Optional[str] = None,
    entry_script_lineno: Optional[int] = None,
) -> None:
    """Log GENERATE: call file, package file, new implementation, transpiled path. All paths relative."""
    source = _relative_display_path(call_site.filename, call_site.lineno, max_len=72)
    generation = f"New implementation (commit {commit_id[:8]})"
    call_file = None
    call_file_link = None
    if entry_script_path:
        call_file = _relative_path_for_display(entry_script_path, entry_script_lineno) if (entry_script_lineno is not None and entry_script_lineno > 0) else _relative_path_for_display(entry_script_path)
        call_file_link = _relative_link_path(entry_script_path, entry_script_lineno if (entry_script_lineno is not None and entry_script_lineno > 0) else None)
    _print_semipy_line_once(
        source, generation, code_path, "yellow", source_link, path_link, code_line_range,
        call_file=call_file,
        call_file_link=call_file_link,
    )


def print_reactive_stale(call_site: SemiCallSite, slot_id: str, reason: str) -> None:
    """Log when a stale slot is detected and will be regenerated."""
    console = get_console()
    loc = _format_location(call_site.filename, call_site.lineno, call_site.func_qualname)
    console.print(f"[yellow]Reactive:[/] slot [cyan]{slot_id[:8]}[/] at {loc} is stale ({reason}); regenerating.")


def print_reactive_cascade(upstream_slot_id: str, affected_count: int) -> None:
    """Log cascade invalidation: upstream change marked N downstream slots stale."""
    console = get_console()
    console.print(f"[yellow]Reactive:[/] upstream [cyan]{upstream_slot_id[:8]}[/] changed; [cyan]{affected_count}[/] downstream slot(s) marked stale.")


def print_reactive_mismatch(slot_id: str, requirement: Any, actual: Any) -> None:
    """Log when downstream requirement triggers upstream ADAPT (output missing required shape)."""
    console = get_console()
    console.print(f"[yellow]Reactive:[/] slot [cyan]{slot_id[:8]}[/] output does not satisfy downstream requirement: required {requirement}, actual {actual}")


def print_reasoning_block(content: str) -> None:
    """Print a reasoning/thinking block in a Rich Panel."""
    if not content.strip():
        return
    console = get_console()
    console.print(Panel(Markdown(content.strip()), title="Reasoning", border_style="dim"))


def print_response_block(content: str) -> None:
    """Print a response text block in a Rich Panel."""
    if not content.strip():
        return
    console = get_console()
    console.print(Panel(Markdown(content.strip()), title="Response", border_style="blue"))


def print_tool_call(tool_name: str, args_preview: str = "") -> None:
    """Legacy one-line log (raw preview). Prefer print_tool_intent_line."""
    console = get_console()
    console.print(f"[dim][Tools][/] [cyan]{tool_name}[/] called {args_preview}")


def print_tool_result(tool_name: str, result_preview: str, success: bool = True) -> None:
    """Legacy one-line log. Prefer print_tool_outcome_line."""
    console = get_console()
    style = "green" if success else "red"
    console.print(f"[dim][Tools][/] [cyan]{tool_name}[/] => [{style}]{result_preview}[/]")


def print_tool_intent_line(
    tool_name: str,
    intent: str,
    *,
    console: Optional[Console] = None,
    show_tool_name: bool = False,
) -> None:
    """Human intent for a tool invocation (no raw JSON)."""
    c = console or get_console()
    if show_tool_name:
        c.print(f"[dim]semipy[/]  [cyan]{tool_name}[/]  {intent}")
    else:
        c.print(f"[dim]semipy[/]  {intent}")


def print_tool_outcome_line(
    outcome: str,
    ok: bool,
    *,
    console: Optional[Console] = None,
) -> None:
    """Structured outcome (not success=True or raw repr)."""
    c = console or get_console()
    style = "green" if ok else "red"
    c.print(f"[dim]semipy[/]  [{style}]{outcome}[/]")


def print_gist_execution(success: bool, stdout: str, stderr: str) -> None:
    """Display gist execution result (stdout/stderr)."""
    console = get_console()
    if success:
        if stdout.strip():
            console.print(Panel(stdout.strip(), title="Gist stdout", border_style="dim"))
    else:
        if stderr.strip():
            console.print(Panel(stderr.strip(), title="Gist stderr", border_style="red"))


def print_decision_explanation(decision: Any, explanation: str) -> None:
    """Log resolution decision and short explanation."""
    console = get_console()
    console.print(f"[dim][semipy][/] Decision: [cyan]{decision}[/] {explanation}")


def print_slot_history(slot: Any, max_entries: int = 20) -> None:
    """Print git-log-style history for a slot (commit id, message, branch)."""
    from semipy.history import Slot
    if not isinstance(slot, Slot):
        return
    console = get_console()
    commits = sorted(slot.commits.values(), key=lambda c: c.timestamp, reverse=True)
    if not commits:
        return
    branch_heads = {b.name: b.head for b in slot.branches.values()}
    lines = [f"  [dim]slot: {slot.function_name_base}[/]"]
    for c in commits[:max_entries]:
        branch_label = ""
        for bname, head in branch_heads.items():
            if head == c.commit_id:
                branch_label = f" [{bname}]"
                break
        lines.append(f"  [cyan]{c.commit_id[:8]}[/] [dim]{c.message}[/] [green]{c.decision}[/]{branch_label}")
    if len(commits) > max_entries:
        lines.append(f"  [dim]... {len(commits) - max_entries} more[/]")
    console.print("\n".join(lines))


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


def print_friendly_exception(exc: BaseException, *, title: str = "Traceback") -> None:
    """Rich traceback (no locals) for debugging generation failures."""
    from rich.traceback import Traceback

    console = get_console()
    w = min(120, console.width) if getattr(console, "width", None) else 120
    tb = Traceback.from_exception(
        type(exc),
        exc,
        exc.__traceback__,
        show_locals=False,
        width=w,
    )
    console.print(Panel(tb, title=title, border_style="red"))


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

    def __init__(self, verbose: bool, use_status_line: bool = True) -> None:
        self._verbose = verbose
        self._use_status_line = use_status_line
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
        self._call_site: Optional[SemiCallSite] = None
        self._stage: str = ""

    def set_call_site(self, call_site: Optional[SemiCallSite]) -> None:
        self._call_site = call_site

    def set_stage(self, stage: str) -> None:
        self._stage = stage

    def _status_message(self, message: str) -> str:
        prefix = ""
        if self._call_site is not None:
            prefix = _format_call_site_short(self._call_site) + " "
        if self._stage:
            prefix = f"[{self._stage}] " + prefix
        return f"[bold blue]{prefix}{message}[/]"

    def update(self, message: str) -> None:
        if not self._verbose or not self._use_status_line:
            return
        console = get_console()
        full = self._status_message(message)
        if self._status is not None:
            self._status.update(full)
        else:
            self._status = console.status(full)
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
        self._stop_status()
        if not self._verbose or self._result is None:
            return
        console = get_console()
        if self._result == "cache_hit" and self._cache_hit is not None:
            spec, entry = self._cache_hit
            cs = spec.call_site
            source = _format_call_source(cs)
            path = _format_cache_path(self._cache_dir, entry)
            _print_semipy_line_once(
                source, "Reused cached implementation.", path, "green",
                source_link=_call_site_file_url(cs.filename, cs.lineno),
                path_link=_file_link_url(path),
            )
            console.print("[dim]  -> its guarantees are on hover in the editor[/]")
        elif self._result == "success":
            source = ""
            source_link = None
            if self._success_call_site is not None:
                cs = self._success_call_site
                source = _format_call_source(cs)
                source_link = _call_site_file_url(cs.filename, cs.lineno)
            path = self._success_display_path or ""
            path_link = _file_link_url(path) if path else None
            _print_semipy_line_once(
                source, "Generated.", path, "green",
                source_link=source_link,
                path_link=path_link,
            )
            console.print("[dim]  -> hover the spec in your editor for why, guarantees, and effects[/]")
        elif self._result == "failure":
            source = ""
            source_link = None
            if self._failure_call_site is not None:
                cs = self._failure_call_site
                source = _format_call_source(cs)
                source_link = _call_site_file_url(cs.filename, cs.lineno)
            msg = self._failure_msg or "validation failed"
            _print_semipy_line_once(source, msg, "", "red", source_link=source_link)
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
def generation_progress(verbose: bool, use_status_line: bool = True):
    """
    Context manager for one generate() run. Yields a GenerationProgress that updates
    a single status line; on exit prints one summary (cache hit panel, success line, or failure).
    Use this to avoid stacking many step lines.
    Set use_status_line=False when Rich Live owns the terminal (timeline + peek).
    """
    progress = GenerationProgress(verbose, use_status_line=use_status_line)
    try:
        yield progress
    finally:
        progress.print_summary()
