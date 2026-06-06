"""Executor role: run a candidate / cached implementation deterministically.

Zero LLM. Wraps ``GistExecutor`` (E2B when configured, local subprocess
otherwise) and maps its ``ExecutionResult`` into a typed ``ExecutionEvidence``,
best-effort parsing emitted ``[{"input":..,"output":..}]`` rows so downstream
roles (verifier, reuse judge) grade against real observed I/O.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from semipy.agents.config import get_config
from semipy.agents.executor import ExecutionResult, GistExecutor
from semipy.orchestration.artifacts import ExecutionEvidence

#: Bound the stdout region scanned for io-pairs so pathological output can't blow
#: up parsing (only the tail is kept -- the batch gist prints results last).
_MAX_STDOUT_SCAN = 262_144  # 256 KiB


def _is_io_row(row: Any) -> bool:
    """An io-pair row must carry both an input and an output."""
    return isinstance(row, dict) and "input" in row and "output" in row


def _parse_io_pairs(stdout: str) -> list[dict]:
    """Best-effort: find the JSON array of ``{input, output}`` rows in ``stdout``.

    Hardened against a candidate's own stdout masquerading as graded evidence:
    rows must carry both ``input`` and ``output`` keys (an unrelated JSON array is
    rejected), the **last** valid array wins (the batch gist prints results after
    any other output), and the scan is size-bounded. Returns ``[]`` when nothing
    qualifies -- never raises.
    """
    if len(stdout) > _MAX_STDOUT_SCAN:
        stdout = stdout[-_MAX_STDOUT_SCAN:]
    candidates: list[str] = []
    whole = stdout.strip()
    if whole.startswith("["):
        candidates.append(whole)
    candidates.extend(
        line.strip() for line in stdout.splitlines() if line.strip().startswith("[")
    )
    found: list[dict] = []
    for chunk in candidates:
        try:
            parsed = json.loads(chunk)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list) and parsed and all(_is_io_row(r) for r in parsed):
            found = parsed  # keep scanning; prefer the last valid io-array
    return found


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
