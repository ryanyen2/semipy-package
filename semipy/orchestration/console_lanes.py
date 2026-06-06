"""Concurrent-role streaming UX (U9): one lane per active role.

The existing ``GenerationStreamView`` assumes a single linear model stream (one
``PhaseState``, one peek deque). When the orchestrator runs roles concurrently
(explorer || version-checker, the verifier's voting fan-out), several roles are
active at once, so this module models N independent lanes -- each with its own
phase and a short rolling tail -- rendered together in one region.

This is the UX substrate; it is additive and does not change the single-stream
path. ``render_lines`` is plain text so the layout is unit-testable without a
terminal. ``make_lanes_sink`` returns ``None`` for non-verbose or non-terminal
(piped/CI) contexts, where callers fall back to plain transient lines.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

ROLE_ORDER = ("explorer", "version-checker", "coder", "executor", "verifier", "surfacer")
TAIL_LINES = 2


@dataclass
class RoleLane:
    name: str
    phase: str = "idle"  # idle | active | done | error
    tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))

    def touched(self) -> bool:
        return self.phase != "idle" or bool(self.tail)


class RoleLanesModel:
    """Tracks per-role phase + rolling output tail for concurrent display."""

    def __init__(self, roles: tuple[str, ...] = ROLE_ORDER) -> None:
        self.lanes: dict[str, RoleLane] = {r: RoleLane(name=r) for r in roles}

    def _lane(self, role: str) -> RoleLane:
        lane = self.lanes.get(role)
        if lane is None:
            lane = RoleLane(name=role)
            self.lanes[role] = lane
        return lane

    def set_phase(self, role: str, phase: str) -> None:
        self._lane(role).phase = phase

    def push(self, role: str, text: str) -> None:
        """Append streamed text to a role's tail, splitting on newlines."""
        lane = self._lane(role)
        if not lane.tail:
            lane.tail.append("")
        parts = text.split("\n")
        lane.tail[-1] += parts[0]
        for part in parts[1:]:
            lane.tail.append(part)

    def active_roles(self) -> list[str]:
        return [name for name, lane in self.lanes.items() if lane.touched()]

    def render_lines(self) -> list[str]:
        """One plain line per touched lane: ``role [phase] last-tail`` (testable)."""
        lines: list[str] = []
        for name, lane in self.lanes.items():
            if not lane.touched():
                continue
            last = lane.tail[-1] if lane.tail else ""
            suffix = f" {last}" if last else ""
            lines.append(f"{name} [{lane.phase}]{suffix}")
        return lines

    def as_renderable(self):
        """Rich renderable for terminal display (one row per touched lane)."""
        from rich.table import Table

        table = Table.grid(padding=(0, 1))
        table.add_column(justify="left")
        table.add_column(justify="left")
        table.add_column(justify="left", overflow="ellipsis", no_wrap=True)
        for name, lane in self.lanes.items():
            if not lane.touched():
                continue
            last = lane.tail[-1] if lane.tail else ""
            table.add_row(name, f"[{lane.phase}]", last)
        return table


def make_lanes_sink(*, verbose: bool, is_terminal: bool) -> Optional[RoleLanesModel]:
    """Return a lanes model for verbose terminals; ``None`` otherwise (plain fallback)."""
    if not verbose or not is_terminal:
        return None
    return RoleLanesModel()
