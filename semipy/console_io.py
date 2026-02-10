"""Rich-based console output for agent steps, validation errors, and confirmations."""
from __future__ import annotations

from typing import Callable, Optional

from rich.console import Console
from rich.panel import Panel

from semipy.types import ValidationResult

_console: Optional[Console] = None


def get_console() -> Console:
    """Return the shared Rich Console (works in terminal, REPL, and Jupyter)."""
    global _console
    if _console is None:
        _console = Console()
    return _console


def step_log(message: str, detail: Optional[str] = None) -> None:
    """Print a step message with optional detail."""
    console = get_console()
    console.print("[bold blue]Step:[/] " + message)
    if detail:
        console.print("  " + detail)


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


def decision_summary(message: str) -> None:
    """Print a short decision summary."""
    console = get_console()
    console.print("[dim]" + message + "[/]")


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
