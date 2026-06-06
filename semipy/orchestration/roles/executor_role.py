"""Executor role: run a candidate / cached implementation deterministically.

Zero LLM. Wraps ``GistExecutor`` (E2B when configured, local subprocess
otherwise) and maps its ``ExecutionResult`` into a typed ``ExecutionEvidence``,
best-effort parsing emitted ``[{"input":..,"output":..}]`` rows so downstream
roles (verifier, reuse judge) grade against real observed I/O.
"""
from __future__ import annotations

import json
from typing import Optional

from semipy.agents.config import get_config
from semipy.agents.executor import ExecutionResult, GistExecutor
from semipy.orchestration.artifacts import ExecutionEvidence


def _parse_io_pairs(stdout: str) -> list[dict]:
    """Best-effort: find a JSON array of ``{input, output}`` rows in ``stdout``.

    Scans whole-string first, then line by line (the batch gist prints one array).
    Returns ``[]`` when nothing parseable is present -- never raises.
    """
    candidates = [stdout.strip()]
    candidates.extend(line.strip() for line in stdout.splitlines())
    for chunk in candidates:
        if not chunk.startswith("["):
            continue
        try:
            parsed = json.loads(chunk)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list) and all(isinstance(r, dict) for r in parsed):
            return parsed
    return []


def _evidence_from_result(result: ExecutionResult) -> ExecutionEvidence:
    io_pairs = _parse_io_pairs(result.stdout or "") if result.success else []
    return ExecutionEvidence(
        success=result.success,
        io_pairs=io_pairs,
        result_repr=result.result_repr,
        stdout=result.stdout or "",
        error=result.error,
    )


def _make_executor(timeout: Optional[int]) -> GistExecutor:
    config = get_config()
    return GistExecutor(
        timeout=timeout if timeout is not None else config.gist_timeout,
        e2b_api_key=config.e2b_api_key,
    )


def run_gist(gist_source: str, *, timeout: Optional[int] = None, cwd: Optional[str] = None) -> ExecutionEvidence:
    """Execute ``gist_source`` synchronously and return typed evidence.

    Synchronous: safe to call from the orchestrator's deterministic steps. From an
    async role already on the shared loop, use ``run_gist_async`` instead
    (``execute_sync`` owns its own loop and would clash with a running one).
    """
    result = _make_executor(timeout).execute_sync(gist_source, cwd=cwd)
    return _evidence_from_result(result)


async def run_gist_async(
    gist_source: str,
    *,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
) -> ExecutionEvidence:
    """Async variant for use inside roles running on the shared event loop."""
    result = await _make_executor(timeout).execute_async(gist_source, cwd=cwd)
    return _evidence_from_result(result)
