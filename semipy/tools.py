"""Deterministic tool system for semi(): SEARCH, RAG, and other tools.

Library builders reference tools in prompts via literal {TOOL()} or {TOOL(arg)}.
In f-strings use double braces so the reference stays literal: {{SEARCH(query)}}.
At generation time, the system injects tool docs so the LLM generates code that
calls the tools at runtime.

Usage in prompts (inside f-string, double braces to avoid interpolation):
    semi(f"look up {{SEARCH(query)}} for {topic}")
    semi(f"augment with {{RAG(context, k=3)}}")
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

TOOL_REF_PATTERN = re.compile(r"\{([A-Z][A-Z0-9_]*)\s*\(([^)]*)\)\}")


def parse_tool_refs(prompt: str) -> list[tuple[str, str]]:
    """Extract tool references from a prompt. Returns list of (tool_name, args_str)."""
    refs: list[tuple[str, str]] = []
    for m in TOOL_REF_PATTERN.finditer(prompt):
        refs.append((m.group(1), m.group(2).strip()))
    return refs


def _firecrawl_search(query: str, limit: int = 3, **kwargs: Any) -> str:
    """SEARCH implementation using Firecrawl. Requires FIRE_CRAWL_API_KEY."""
    api_key = os.getenv("FIRE_CRAWL_API_KEY") or os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise ValueError(
            "SEARCH requires FIRE_CRAWL_API_KEY or FIRECRAWL_API_KEY. "
            "Set in .env or register_tool('SEARCH', your_fn)."
        )
    try:
        from firecrawl import Firecrawl

        fc = Firecrawl(api_key=api_key)
        result = fc.search(query=query, limit=limit)
        if not result:
            return ""
        web = getattr(result, "web", None) or (
            result.get("web", []) if isinstance(result, dict) else []
        )
        if not web:
            return ""
        parts = []
        for hit in web[:limit]:
            title = getattr(hit, "title", None) or (hit.get("title", "") if isinstance(hit, dict) else "")
            desc = getattr(hit, "description", None) or getattr(hit, "snippet", None) or (hit.get("description", hit.get("snippet", "")) if isinstance(hit, dict) else "")
            url = getattr(hit, "url", None) or (hit.get("url", "") if isinstance(hit, dict) else "")
            parts.append(f"{title}: {desc}" + (f" ({url})" if url else ""))
        return "\n".join(parts) if parts else ""
    except ImportError:
        raise ImportError(
            "firecrawl-py required for SEARCH. Install: pip install firecrawl-py"
        )


def _rag_from_csv(query: str, k: int = 3, data_path: Optional[str] = None, **kwargs: Any) -> list[str]:
    """RAG implementation: retrieve relevant rows from a CSV file. data_path from kwargs or SEMIPY_RAG_DATA_PATH."""
    path_str = data_path or os.getenv("SEMIPY_RAG_DATA_PATH")
    if not path_str:
        raise ValueError(
            "RAG requires data_path argument or SEMIPY_RAG_DATA_PATH env. "
            "E.g. RAG(query, k=3, data_path='path/to/data.csv')"
        )
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"RAG data path not found: {path_str}")
    query_words = set(query.lower().split())
    results: list[tuple[int, str, dict]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    for i, row in enumerate(rows):
        text = " ".join(str(v) for v in row.values()).lower()
        score = sum(1 for w in query_words if w in text)
        if score > 0:
            row_str = "; ".join(f"{k}={v}" for k, v in row.items())
            results.append((score, row_str, row))
    results.sort(key=lambda x: -x[0])
    return [r[1] for r in results[:k]]


_TOOL_IMPLS: dict[str, Callable[..., Any]] = {
    "SEARCH": _firecrawl_search,
    "RAG": _rag_from_csv,
}


def SEARCH(query: str, **kwargs: Any) -> str:
    """
    Web search via Firecrawl. Use in prompts as {{SEARCH(query)}} (double braces in f-strings).
    Returns concatenated title+description from web results.
    """
    impl = _TOOL_IMPLS["SEARCH"]
    return impl(query, **kwargs)


def RAG(query: str, k: int = 3, data_path: Optional[str] = None, **kwargs: Any) -> list[str]:
    """
    Retrieve relevant rows from a CSV. Use as {{RAG(query, k=5, data_path=path)}}.
    Returns list of matching row strings.
    """
    impl = _TOOL_IMPLS["RAG"]
    return impl(query, k=k, data_path=data_path, **kwargs)


def register_tool(name: str, impl: Callable[..., Any]) -> None:
    """Register a custom tool implementation."""
    _TOOL_IMPLS[name] = impl


def tool_docstring_for_prompt(tool_names: set[str]) -> str:
    """Generate the tool documentation block to inject into the system prompt."""
    doc = {
        "SEARCH": (
            "SEARCH(query: str) -> str: Web search via Firecrawl. Use when the prompt references {SEARCH(...)}. "
            "Returns concatenated title+description from web results. Import: from semipy.tools import SEARCH"
        ),
        "RAG": (
            "RAG(query: str, k: int = 3, data_path: str = None) -> list[str]: Retrieve rows from CSV. "
            "Use when the prompt references {RAG(...)}. data_path required (or SEMIPY_RAG_DATA_PATH). "
            "Import: from semipy.tools import RAG"
        ),
    }
    if not tool_names:
        return ""
    lines = [
        "",
        "Available tools (use when prompt references them). Import from semipy.tools; external calls via these tools are allowed:",
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
    if not refs:
        return system_prompt
    tool_names = {name for name, _ in refs}
    extra = tool_docstring_for_prompt(tool_names)
    if extra:
        return system_prompt + "\n" + extra
    return system_prompt
