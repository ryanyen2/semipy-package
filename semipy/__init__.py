"""
Runtime semiformal system: @semiformal and semi() for semantically underspecified logic.

Decorated functions call semi(f\"...\") or semi.name(...); the first run generates
and caches a Python function via an agentic pipeline (OpenRouter + pydantic_ai);
later runs reuse the cached implementation.
"""
from __future__ import annotations

import importlib
from typing import Any

from semipy.decorator import semiformal
from semipy.semi_fn import semi
from semipy.agents.config import SemiConfig, configure, get_config
from semipy.types import Decision, SemiCallError, SemiGenerationError, compute_spec_equivalence_key
from semipy.agents.tools import register_tool, parse_tool_refs

_LAZY: dict[str, tuple[str, str]] = {
    "DependencyGraph": ("semipy.reactivity", "DependencyGraph"),
    "SlotRef": ("semipy.reactivity", "SlotRef"),
    "DataFlow": ("semipy.reactivity", "DataFlow"),
    "attach_producer_flow": ("semipy.reactivity", "attach_producer_flow"),
    "load_library": ("semipy.library", "load_library"),
    "run_sleep_phase": ("semipy.library", "run_sleep_phase"),
    "AbstractionLibrary": ("semipy.library", "AbstractionLibrary"),
    "LibraryPrimitive": ("semipy.library", "LibraryPrimitive"),
    "ASTPattern": ("semipy.library", "ASTPattern"),
    "GistBuilder": ("semipy.agents.gist", "GistBuilder"),
    "Gist": ("semipy.agents.gist", "Gist"),
    "GistExecutor": ("semipy.agents.executor", "GistExecutor"),
    "ExecutionResult": ("semipy.agents.executor", "ExecutionResult"),
    "SemiAgentDeps": ("semipy.models", "SemiAgentDeps"),
    "DocumentContextResult": ("semipy.models", "DocumentContextResult"),
    "ProfileDataResult": ("semipy.models", "ProfileDataResult"),
    "GistRunResult": ("semipy.models", "GistRunResult"),
    "OutputValidationResult": ("semipy.models", "OutputValidationResult"),
    "SlotContract": ("semipy.contract", "SlotContract"),
    "ContractCase": ("semipy.contract", "ContractCase"),
    "ChangeRecord": ("semipy.contract.change", "ChangeRecord"),
    # Effects subsystem (reified real-world effects).
    "Effect": ("semipy.effects", "Effect"),
    "EffectScript": ("semipy.effects", "EffectScript"),
    "EffectResult": ("semipy.effects", "EffectResult"),
    "EffectRecorder": ("semipy.effects", "EffectRecorder"),
    "ArtifactBackend": ("semipy.effects", "ArtifactBackend"),
    "MemoryArtifactBackend": ("semipy.effects", "MemoryArtifactBackend"),
    "SqliteArtifactBackend": ("semipy.effects", "SqliteArtifactBackend"),
    "register_artifact_backend": ("semipy.effects", "register_artifact_backend"),
    "resolve_backend": ("semipy.effects", "resolve_backend"),
    "revert": ("semipy.effects", "revert"),
    "provenance_for": ("semipy.effects", "provenance_for"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        module_path, attr = _LAZY[name]
        mod = importlib.import_module(module_path)
        obj = getattr(mod, attr)
        globals()[name] = obj
        return obj
    raise AttributeError(f"module 'semipy' has no attribute {name!r}")


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
    "SlotContract",
    "ContractCase",
    "ChangeRecord",
    "Effect",
    "EffectScript",
    "EffectResult",
    "EffectRecorder",
    "ArtifactBackend",
    "MemoryArtifactBackend",
    "SqliteArtifactBackend",
    "register_artifact_backend",
    "resolve_backend",
    "revert",
    "provenance_for",
]
