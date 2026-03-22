"""
Terminal (and Jupyter) Live layout: phase strip + rolling peek of streamed model tokens.

Peek uses a deque of visible lines (see examples/rich/vertical_window.py) plus Syntax
highlighting when the tail looks like code.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


@dataclass
class PhaseState:
    """Horizontal phase strip: past segments dim, current bold, future dim."""

    order: tuple[str, ...] = ("Resolve", "Model", "Tools", "Validate", "Done")
    active_index: int = 1

    def set_active(self, name: str) -> None:
        if name in self.order:
            self.active_index = self.order.index(name)

    def render_line(self) -> Text:
        parts: list[str] = []
        for i, name in enumerate(self.order):
            if i < self.active_index:
                parts.append(f"[dim]{name}[/]")
            elif i == self.active_index:
                parts.append(f"[bold]{name}[/]")
            else:
                parts.append(f"[dim]{name}[/]")
            if i < len(self.order) - 1:
                parts.append("[dim] | [/]")
        return Text.from_markup("".join(parts))


def _push_chunk(lines: deque[str], chunk: str) -> None:
    """Append streaming chunk into a bounded deque of logical lines (vertical_window pattern)."""
    if not lines:
        lines.append("")
    parts = chunk.split("\n")
    lines[-1] += parts[0]
    for part in parts[1:]:
        lines.append(part)


def _peek_visible_lines(lines: deque[str], visible: int) -> list[str]:
    """Pad or trim to exactly `visible` logical lines (vertical_window pattern)."""
    raw = list(lines)
    if len(raw) < visible:
        return [""] * (visible - len(raw)) + raw
    return raw[-visible:]


def _render_peek_body(visible: list[str], *, lexer: str) -> Syntax | Text:
    code = "\n".join(visible)
    if not code.strip():
        return Text("waiting for streamed tokens…[/]", overflow="crop")
    try:
        return Syntax(
            code,
            lexer,
            theme="monokai",
            line_numbers=False,
            word_wrap=True,
        )
    except Exception:
        return Text(code, overflow="crop")


def _guess_lexer(tail_text: str) -> str:
    s = tail_text.lstrip()
    if "def " in s or "import " in s or "class " in s or "return " in s:
        return "python"
    return "text"


class GenerationStreamView:
    """
    Live-updating phase strip + peek window.
    Tool summaries should use ``live.console.print`` so they scroll above the live region.
    """

    def __init__(
        self,
        console: Console,
        peek_lines: int,
        *,
        enabled: bool,
        show_timeline: bool = True,
    ) -> None:
        self._console = console
        self._peek_lines = max(1, peek_lines)
        self._enabled = enabled
        self._show_timeline = show_timeline
        self._live: Optional[Live] = None
        self._line_deque: deque[str] = deque([""], maxlen=max(8, peek_lines * 2))
        self._stream_kind: str = "output"
        self._phase = PhaseState()
        self._start_t: float = 0.0
        self._show_elapsed = False
        self._renderable: Optional[Group | Panel] = None

    @property
    def live(self) -> Optional[Live]:
        return self._live

    @property
    def console(self) -> Console:
        return self._live.console if self._live is not None else self._console

    def phase(self) -> PhaseState:
        return self._phase

    def set_show_elapsed(self, show: bool) -> None:
        self._show_elapsed = show

    def _build_renderable(self) -> Group | Panel:
        visible = _peek_visible_lines(self._line_deque, self._peek_lines)
        lexer = _guess_lexer("\n".join(visible))
        body = _render_peek_body(visible, lexer=lexer)
        subtitle = f"{self._stream_kind} — last {self._peek_lines} lines"
        peek_panel = Panel(
            body,
            title="Stream",
            subtitle=subtitle,
            border_style="cyan",
            padding=(0, 1),
        )
        if not self._show_timeline:
            return peek_panel
        strip = self._phase.render_line()
        if self._show_elapsed:
            sec = max(0.0, time.monotonic() - self._start_t)
            strip = Text.assemble(strip, "  ", Text(f"{sec:0.1f}s", style="dim"))
        return Group(Padding(strip, (0, 1)), peek_panel)

    def _refresh_live(self) -> None:
        if self._live is None:
            return
        self._renderable = self._build_renderable()
        self._live.update(self._renderable, refresh=True)

    def __enter__(self) -> GenerationStreamView:
        if not self._enabled:
            return self
        self._start_t = time.monotonic()
        self._line_deque = deque([""], maxlen=max(8, self._peek_lines * 2))
        self._renderable = self._build_renderable()
        self._live = Live(
            self._renderable,
            console=self._console,
            refresh_per_second=12,
            vertical_overflow="crop",
            auto_refresh=False,
            transient=False,
        )
        self._live.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        if self._live is not None:
            try:
                self._live.__exit__(*args)
            except Exception:
                pass
            self._live = None

    def set_active_phase(self, name: str) -> None:
        self._phase.set_active(name)
        self._refresh_live()

    def append_stream_delta(self, delta: str, *, kind: str = "output") -> None:
        if not self._enabled or not delta:
            return
        self._stream_kind = "reasoning" if kind == "thinking" else "output"
        _push_chunk(self._line_deque, delta)
        if self._live is not None:
            self._refresh_live()

    def clear_stream_buffer(self) -> None:
        self._line_deque = deque([""], maxlen=max(8, self._peek_lines * 2))
        self._stream_kind = "output"
        if self._live is not None:
            self._refresh_live()


class JupyterStreamPeek:
    """
    Throttled peek for Jupyter when Rich Live is unavailable: same deque + Syntax as
    ``GenerationStreamView``, but redraws **in place** via ``ipywidgets.Output`` +
    ``clear_output`` so the cell does not accumulate one Panel per token chunk.

    Without ipywidgets, falls back to printing to the shared Rich console (may append).
    """

    def __init__(self, console: Console, peek_lines: int) -> None:
        self._console = console
        self._peek_lines = max(1, peek_lines)
        self._line_deque: deque[str] = deque([""], maxlen=max(8, peek_lines * 2))
        self._stream_kind = "output"
        self._last_emit = 0.0
        self._chars = 0
        self._peek_out: Optional[Any] = None

    @property
    def console(self) -> Console:
        return self._console

    def set_active_phase(self, _name: str) -> None:
        """Phase strip is terminal-only; Jupyter uses the stream panel only."""

    def _emit_panel(self) -> None:
        visible = _peek_visible_lines(self._line_deque, self._peek_lines)
        lexer = _guess_lexer("\n".join(visible))
        body = _render_peek_body(visible, lexer=lexer)
        subtitle = f"{self._stream_kind} — last {self._peek_lines} lines (Jupyter)"
        panel = Panel(
            body,
            title="Stream",
            subtitle=subtitle,
            border_style="cyan",
            padding=(0, 1),
        )
        if self._peek_out is not None:
            from IPython.display import clear_output

            with self._peek_out:
                clear_output(wait=True)
                self._console.print(panel)
        else:
            self._console.print(panel)

    def append_stream_delta(self, delta: str, *, kind: str = "output") -> None:
        if not delta:
            return
        self._stream_kind = "reasoning" if kind == "thinking" else "output"
        _push_chunk(self._line_deque, delta)
        self._chars += len(delta)
        now = time.monotonic()
        if self._chars < 64 and (now - self._last_emit) < 0.2:
            return
        self._chars = 0
        self._last_emit = now
        self._emit_panel()

    def clear_stream_buffer(self) -> None:
        self._line_deque = deque([""], maxlen=max(8, self._peek_lines * 2))
        self._stream_kind = "output"
        self._chars = 0
        if self._peek_out is not None:
            from IPython.display import clear_output

            with self._peek_out:
                clear_output(wait=True)

    def __enter__(self) -> JupyterStreamPeek:
        try:
            import ipywidgets as ipw
            from IPython.display import display

            self._peek_out = ipw.Output()
            display(self._peek_out)
        except Exception:
            self._peek_out = None
        return self

    def __exit__(self, *args: object) -> None:
        if self._chars > 0:
            self._chars = 0
            self._emit_panel()
        return None
