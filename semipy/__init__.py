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
from semipy.interpreted import InterpretedOp, interpreted
from semipy.agents.config import SemiConfig, configure, get_config
from semipy.types import Decision, SemiCallError, SemiGenerationError, compute_spec_equivalence_key
from semipy.agents.tools import register_tool, parse_tool_refs

_LAZY: dict[str, tuple[str, str]] = {
    "DependencyGraph": ("semipy.reactivity", "DependencyGraph"),
    "SlotRef": ("semipy.reactivity", "SlotRef"),
    "DataFlow": ("semipy.reactivity", "DataFlow"),
    "attach_producer_flow": ("semipy.reactivity", "attach_producer_flow"),
    "GistExecutor": ("semipy.agents.executor", "GistExecutor"),
    "ExecutionResult": ("semipy.agents.executor", "ExecutionResult"),
    "ContainerKernelExecutor": ("semipy.kernel_container", "ContainerKernelExecutor"),
    "SemiAgentDeps": ("semipy.models", "SemiAgentDeps"),
    "SlotContract": ("semipy.contract", "SlotContract"),
    "ContractCase": ("semipy.contract", "ContractCase"),
    "ChangeRecord": ("semipy.contract.change", "ChangeRecord"),
    # Effects subsystem (reified real-world effects).
    "Effect": ("semipy.effects", "Effect"),
    "EffectScript": ("semipy.effects", "EffectScript"),
    "EffectResult": ("semipy.effects", "EffectResult"),
    "EffectRefused": ("semipy.effects", "EffectRefused"),
    "EffectRecorder": ("semipy.effects", "EffectRecorder"),
    "ArtifactBackend": ("semipy.effects", "ArtifactBackend"),
    "MemoryArtifactBackend": ("semipy.effects", "MemoryArtifactBackend"),
    "SqliteArtifactBackend": ("semipy.effects", "SqliteArtifactBackend"),
    "ExternalArtifactBackend": ("semipy.effects", "ExternalArtifactBackend"),
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
    "interpreted",
    "InterpretedOp",
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
    "GistExecutor",
    "ExecutionResult",
    "ContainerKernelExecutor",
    "SemiAgentDeps",
    "SlotContract",
    "ContractCase",
    "ChangeRecord",
    "Effect",
    "EffectScript",
    "EffectResult",
    "EffectRefused",
    "EffectRecorder",
    "ArtifactBackend",
    "MemoryArtifactBackend",
    "SqliteArtifactBackend",
    "ExternalArtifactBackend",
    "register_artifact_backend",
    "resolve_backend",
    "revert",
    "provenance_for",
]
