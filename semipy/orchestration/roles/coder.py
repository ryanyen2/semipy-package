"""Coder role: generate the implementation, return a typed GenerationResult.

With langroid dropped, the "bridge" is a thin async role over the existing
``SemiAgent`` (the pydantic_ai + OpenAI Responses generator, unchanged) -- so
generation keeps its tuned behavior, retry loop, and reasoning-id continuity. The
role projects the resulting ``CacheEntry`` into the JSON-safe ``GenerationResult``
handoff; the live ``CacheEntry`` (with its compiled function) stays with the
caller for commit/dispatch.

An ``agent`` may be injected (tests pass a stub); otherwise a default
``SemiAgent`` is constructed. Generation requires an API key -- the coder does
not abstain (a slot that routed to GENERATE has no cached implementation to fall
back to); ``SemiAgent`` raises a clear error when the key is missing.
"""
from __future__ import annotations

import ast
from typing import Any, Optional

from semipy.orchestration.artifacts import GenerationResult


def _function_name(source: str) -> Optional[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node.name
    return None


def project_entry(entry: Any, *, decision: Any = None, commit_id: Optional[str] = None) -> GenerationResult:
    """Project a ``CacheEntry`` (or stub) into the typed ``GenerationResult``."""
    source = getattr(entry, "generated_source", "") or ""
    decision_str = getattr(decision, "value", str(decision)) if decision is not None else None
    return GenerationResult(
        generated_source=source,
        function_name=_function_name(source),
        decision=decision_str,
        commit_id=commit_id,
    )


def _agent(agent: Any, spec: Any) -> Any:
    if agent is not None:
        return agent
    from semipy.agents.agent import SemiAgent

    return SemiAgent(max_retries=getattr(spec, "max_retries", None))


def code(spec: Any, *, agent: Any = None) -> GenerationResult:
    """Synchronous coder: run generation and return a typed result."""
    entry = _agent(agent, spec).generate(spec)
    return project_entry(entry, decision=getattr(spec, "decision", None))


async def code_async(spec: Any, *, agent: Any = None) -> GenerationResult:
    """Async coder for use on the shared event loop."""
    entry = await _agent(agent, spec).generate_async(spec)
    return project_entry(entry, decision=getattr(spec, "decision", None))
