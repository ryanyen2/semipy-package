"""Parallel fan-out for read-only roles (KTD4).

Independent read-only roles (code-explorer, version-checker evidence-gathering)
run concurrently on the shared background event loop via ``asyncio.gather``, not
langroid's batch helpers (which own their own loop). Synchronous role callables
are dispatched with ``asyncio.to_thread`` so they overlap. State-mutating work is
NOT run here -- writers stay serial behind the portal lock ("serialize the
writers, parallelize the readers").

A thunk that raises degrades to ``None`` (the whole gather never fails), so one
read-only role failing cannot abort the others or the pipeline.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from semipy.orchestration.runtime import embed_run


def gather_readonly(thunks: list[Callable[[], Any]]) -> list[Any]:
    """Run read-only ``thunks`` concurrently; return results in order (failures -> None)."""
    if not thunks:
        return []

    async def _all() -> list[Any]:
        coros = [asyncio.to_thread(thunk) for thunk in thunks]
        return await asyncio.gather(*coros, return_exceptions=True)

    results = embed_run(_all())
    return [None if isinstance(r, BaseException) else r for r in results]
