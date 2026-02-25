"""
Runtime semiformal system: @semiformal and semi() for semantically underspecified logic.

Decorated functions call semi(f\"...\") or semi.name(...); the first run generates
and caches a Python function via an agentic pipeline (OpenRouter + pydantic_ai);
later runs reuse the cached implementation.
"""
from semipy.decorator import semiformal
from semipy.semi_fn import semi
from semipy.agents.config import SemiConfig, configure, get_config
from semipy.types import Decision, SemiCallError, SemiGenerationError
from semipy.agents.tools import register_tool, parse_tool_refs
from semipy.reactivity import DependencyGraph, SlotRef, DataFlow
from semipy.agents.gist import GistBuilder, Gist
from semipy.agents.executor import GistExecutor, ExecutionResult
from semipy.models import (
    SemiAgentDeps,
    ProfileDataResult,
    GistRunResult,
    OutputValidationResult,
)

__all__ = [
    "semiformal",
    "semi",
    "SemiConfig",
    "configure",
    "get_config",
    "Decision",
    "SemiCallError",
    "SemiGenerationError",
    "register_tool",
    "parse_tool_refs",
    "DependencyGraph",
    "SlotRef",
    "DataFlow",
    "GistBuilder",
    "Gist",
    "GistExecutor",
    "ExecutionResult",
    "SemiAgentDeps",
    "ProfileDataResult",
    "GistRunResult",
    "OutputValidationResult",
]
