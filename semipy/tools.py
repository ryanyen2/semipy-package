"""Deterministic tool system for semi(): SEARCH, RAG, and other tools.

Library builders and users reference tools in prompts via {TOOL()} or {TOOL(arg)}.
At generation time, the system injects tool docs into the prompt so the LLM
generates code that uses the correct tools.

Usage in prompts:
    semi("look up {SEARCH(query)} for this topic")
    semi("augment with {RAG(context, k=3)}")
    semi("filter where {SEARCH(value)} returns relevant results")

Import tools from semipy for use in your library:
    from semipy import semi, SEARCH, RAG
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional


TOOL_REF_PATTERN = re.compile(r"\[([A-Z][A-Z0-9_]*)\s*\(([^)]*)\)\]")


def parse_tool_refs(prompt: str) -> list[tuple[str, str]]:
    """Extract tool references from a prompt. Returns list of (tool_name, args_str)."""
    refs: list[tuple[str, str]] = []
    print('raw prompt: ', prompt)
    for m in TOOL_REF_PATTERN.finditer(prompt):
        refs.append((m.group(1), m.group(2).strip()))
    return refs


def _search_impl(query: str, **kwargs: Any) -> str:
    """Default search implementation. Override via semipy.tools.register_tool."""
    return f"[Search: {query}. Register real impl via semipy.tools.register_tool('SEARCH', fn)]"


def _rag_impl(query: str, k: int = 3, **kwargs: Any) -> list[str]:
    """Default RAG implementation. Override via semipy.tools.register_tool."""
    return [f"[RAG k={k}: {query}. Register via semipy.tools.register_tool('RAG', fn)]"]


_TOOL_IMPLS: dict[str, Callable[..., Any]] = {
    "SEARCH": _search_impl,
    "RAG": _rag_impl,
}


def SEARCH(query: str, **kwargs: Any) -> str:
    """
    Semantic search tool. Use in prompts as {SEARCH(query)}.
    Returns text results from web/knowledge retrieval.
    """
    impl = _TOOL_IMPLS.get("SEARCH", _search_impl)
    return impl(query, **kwargs)


def RAG(query: str, k: int = 3, **kwargs: Any) -> list[str]:
    """
    Retrieval-augmented lookup. Use in prompts as {RAG(query)} or {RAG(query, k=5)}.
    Returns list of relevant passages.
    """
    impl = _TOOL_IMPLS.get("RAG", _rag_impl)
    return impl(query, k=k, **kwargs)


def register_tool(name: str, impl: Callable[..., Any]) -> None:
    """Register a custom tool implementation."""
    _TOOL_IMPLS[name] = impl


def tool_docstring_for_prompt(tool_names: set[str]) -> str:
    """Generate the tool documentation block to inject into the system prompt."""
    doc = {
        "SEARCH": (
            "SEARCH(query: str) -> str: Semantic search. Use when the prompt references {SEARCH(...)}. "
            "Returns concatenated text from web/knowledge search. "
            "Import: from semipy.tools import SEARCH"
        ),
        "RAG": (
            "RAG(query: str, k: int = 3) -> list[str]: Retrieval-augmented lookup. "
            "Use when the prompt references {RAG(...)}. Returns list of relevant passages. "
            "Import: from semipy.tools import RAG"
        ),
    }
    if not tool_names:
        return ""
    lines = [
        "",
        "Available tools (use when prompt references them):",
    ]
    for name in sorted(tool_names):
        if name in doc:
            lines.append(f"- {doc[name]}")
        else:
            lines.append(f"- {name}: custom tool (see semipy.tools.register_tool)")
    return "\n".join(lines)


def inject_tools_into_system_prompt(system_prompt: str, user_prompt: str) -> str:
    """If user_prompt contains {TOOL(...)} refs, append tool docs to system prompt."""
    refs = parse_tool_refs(user_prompt)
    print('tools used in the user prompt: ', refs)
    if not refs:
        return system_prompt
    tool_names = {name for name, _ in refs}
    extra = tool_docstring_for_prompt(tool_names)
    if extra:
        return system_prompt + "\n" + extra
    return system_prompt
