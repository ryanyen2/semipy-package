"""Multi-role orchestration pipeline for semipy generation.

This package replaces the procedural orchestration that lived inline in
``slot_resolver.execute_slot`` with an explicit, code-driven sequence of named
roles (orchestrator, code-explorer, version-checker, coder, executor, verifier,
surfacer) exchanging typed artifacts. It is intentionally framework-light: roles
are plain typed async callables driven by a deterministic orchestrator, running
on the shared background event loop, with every LLM-backed role degrading to a
deterministic default when no API key is configured.

See ``docs/plans/2026-06-06-001-feat-multi-agent-orchestration-pipeline-plan.md``
for the full design. (langroid was evaluated and dropped: its transitive tree is
too heavy for a distributed library; the orchestrator is built in-house over the
existing ``pydantic_ai`` Responses stack.)
"""
from __future__ import annotations

from semipy.orchestration.runtime import embed_run, make_responses_model

__all__ = ["embed_run", "make_responses_model"]
