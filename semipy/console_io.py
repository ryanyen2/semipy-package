"""
Rich-based console output for the semiformal pipeline.

Provides one-line DAG logs (reuse/adapt/generate), validation error panels,
progress status, and confirmation prompts.
"""
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
    Decision,
    GenerationSpec,
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


def decision_description(decision: Decision) -> str:
    """Human-readable explanation of the resolution decision."""
    if decision == Decision.REUSE:
        return "Reuse cached implementation"
    if decision == Decision.ADAPT:
        return "Adapt from previous implementation"
    if decision == Decision.FORK:
        return "New branch (structure changed)"
    if decision == Decision.GENERATE:
        return "Generate new implementation"
    if decision == Decision.MERGE:
        return "Merge branches"
    return str(decision.value)


def _format_location(filename: str, lineno: int, func_qualname: str) -> str:
    """Short location string: file:line (function)."""
    from os.path import basename
    f = basename(filename) if filename else "<unknown>"
    fn = func_qualname or "?"
    return f"{f}:{lineno} ({fn})"


def _call_site_file_url(filename: str, lineno: int) -> str:
    """file:// URL for opening at line (IDE-friendly). Uses resolved path for link target."""
    if not filename:
        return ""
    # p = Path(filename).resolve()
    # use relative path instead of resolved path
    p = Path(filename).relative_to(Path.cwd())
    try:
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


def _short_display_path(full_path: str) -> str:
    """Short path for one-line display (e.g. .../runtime/module.semi.py)."""
    try:
        p = Path(full_path).resolve()
        parts = p.parts
        if "runtime" in parts:
            i = parts.index("runtime")
            return ".../" + "/".join(parts[i:]) if i < len(parts) else p.name
        return p.name if len(p.name) <= 52 else f"...{p.name[-48:]}"
    except Exception:
        return full_path if len(full_path) <= 52 else f"...{full_path[-48:]}"


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


# Dedupe: same (call_file, source, generation, path) only printed once per process.
_semipy_log_printed: set[tuple[str, str, str, str]] = set()


def _format_call_source(call_site: SemiCallSite) -> str:
    """Human-readable call site: relative path and line (function)."""
    loc = _relative_display_path(call_site.filename, call_site.lineno, max_len=72)
    fn = call_site.func_qualname or "?"
    return f"{loc} ({fn})"


def _format_call_site_short(call_site: SemiCallSite) -> str:
    """Short call site for progress line: file:line (func)."""
    from os.path import basename
    f = basename(call_site.filename) if call_site.filename else "<unknown>"
    fn = call_site.func_qualname or "?"
    return f"{f}:{call_site.lineno} ({fn})"


def print_pipeline_log(
    call_site: Optional[SemiCallSite],
    stage: str,
    message: str,
) -> None:
    """Print one pipeline line: [semipy] [stage] call_site message (cohesive format)."""
    console = get_console()
    loc = _format_call_site_short(call_site) if call_site else "?"
    console.print(
        f"[dim][semipy][/] [cyan][{stage}][/] {loc} {message}",
        no_wrap=True,
    )


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
    console.print(Panel(content.strip(), title="Reasoning", border_style="dim"))


def print_response_block(content: str) -> None:
    """Print a response text block in a Rich Panel."""
    if not content.strip():
        return
    console = get_console()
    console.print(Panel(content.strip(), title="Response", border_style="blue"))


def print_tool_call(tool_name: str, args_preview: str = "") -> None:
    """One-line log for a tool call."""
    console = get_console()
    console.print(f"[dim][Tools][/] [cyan]{tool_name}[/] called {args_preview}")


def print_tool_result(tool_name: str, result_preview: str, success: bool = True) -> None:
    """One-line log for a tool result."""
    console = get_console()
    style = "green" if success else "red"
    console.print(f"[dim][Tools][/] [cyan]{tool_name}[/] => [{style}]{result_preview}[/]")


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
    from semipy.dag import Commit, Slot
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
        if not self._verbose:
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
        import sys
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
