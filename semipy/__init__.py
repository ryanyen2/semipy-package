"""
Runtime semiformal system: @semiformal and semi() for semantically underspecified logic.

Decorated functions call semi(f\"...\") or semi.name(...); the first run generates
and caches a Python function via an agentic pipeline (OpenRouter + pydantic_ai);
later runs reuse the cached implementation.
"""
from semipy.decorator import semiformal
from semipy.semi_fn import semi
from semipy.agents.config import SemiConfig, configure, get_config
from semipy.types import Decision, SemiCallError, SemiGenerationError, compute_spec_equivalence_key
from semipy.agents.tools import register_tool, parse_tool_refs
from semipy.reactivity import DependencyGraph, SlotRef, DataFlow, attach_producer_flow
from semipy.library import (
    load_library,
    run_sleep_phase,
    AbstractionLibrary,
    LibraryPrimitive,
    ASTPattern,
)
from semipy.agents.gist import GistBuilder, Gist
from semipy.agents.executor import GistExecutor, ExecutionResult
from semipy.models import (
    SemiAgentDeps,
    DocumentContextResult,
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
    "compute_spec_equivalence_key",
    "register_tool",
    "parse_tool_refs",
    "DependencyGraph",
    "SlotRef",
    "DataFlow",
    "attach_producer_flow",
    "DocumentContextResult",
    "GistBuilder",
    "Gist",
    "GistExecutor",
    "ExecutionResult",
    "SemiAgentDeps",
    "ProfileDataResult",
    "GistRunResult",
    "OutputValidationResult",
    "load_library",
    "run_sleep_phase",
    "AbstractionLibrary",
    "LibraryPrimitive",
    "ASTPattern",
]
