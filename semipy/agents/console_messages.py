"""
Human-readable summaries for agent tool calls and results (no raw JSON dumps).

The pydantic_ai agent exposes a single tool, ``execute_action_program``; these
pure helpers turn its call/return into one-line human intents for the console
stream. A defensive generic fallback covers any other tool name.
"""
from __future__ import annotations

import json
from typing import Any


def format_tool_call_line(tool_name: str, args: Any, *, debug: bool = False) -> str:
    """Single-line intent for a tool invocation (no generated source body)."""
    if tool_name == "execute_action_program":
        return "Draft the function"
    if debug:
        try:
            return f"{tool_name} {json.dumps(args, default=str)[:200]}"
        except Exception:
            return f"{tool_name} (args present)"
    return f"Run tool {tool_name}"


def format_tool_result_line(tool_name: str, content: Any, *, debug: bool = False) -> tuple[str, bool]:
    """One-line outcome for a tool return. Returns ``(summary, ok)``."""
    if tool_name == "execute_action_program":
        return ("function drafted", True)
    if debug:
        raw = repr(content)
        if len(raw) > 200:
            raw = raw[:197] + "…"
        return (raw, True)
    raw = str(content) if content is not None else ""
    if len(raw) > 100:
        raw = raw[:97] + "…"
    return (raw or "(empty)", True)


# ---------------------------------------------------------------------------
# End-of-run annealing report (R7): one compact line per slot with ledger
# activity since the run started. Pure formatting; console_core.py aggregates
# the rows and decides whether/where to print them.
# ---------------------------------------------------------------------------


def format_annealing_header() -> str:
    return f"{'slot':<14}{'decision':<10}{'+cases':>7}{'deopts':>7}{'disputes':>9}{'quarantines':>12}"


def format_annealing_row(row: dict[str, Any]) -> str:
    """One line of the annealing report: slot, decision, cases added, deopts,
    disputes, quarantines -- the per-slot ledger delta since the run started."""
    return (
        f"{row['slot_id'][:12]:<14}{row['decision']:<10}{row['cases_added']:>7}"
        f"{row['deopts']:>7}{row['disputes']:>9}{row['quarantines']:>12}"
    )
