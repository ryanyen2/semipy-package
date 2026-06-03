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
