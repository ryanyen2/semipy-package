"""Orchestrator: the code-driven driver for the generation pipeline's stages.

This is the seam that progressively takes ownership of the stages currently
inlined in ``slot_resolver._execute_slot_locked``. It is deliberately **not** an
autonomous agent (KTD2): control flow stays in plain Python; each stage delegates
to a focused role callable and returns a typed artifact or a live result the spine
consumes.

Scope so far (U3): the routing decision point is owned here as a named ``route``
stage. The remaining stages (explorer/coder/verifier/surfacer) are wired in
U4-U8, and the KTD7 lock-narrowing -- hoisting the generated-function call out of
the per-portal lock -- is deferred until an end-to-end integration harness exists
to verify it (the offline unit suite cannot exercise the full ``execute_slot``
path, which requires an API key, so a blind hoist is unverifiable today).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from semipy.orchestration.artifacts import VersionContext
from semipy.orchestration.roles import version_checker


class Orchestrator:
    """Drives the named stages of one slot execution.

    Constructed per ``execute_slot`` call with the resolved project identity. The
    spine calls into the orchestrator for stages it has taken ownership of; the
    rest of the body still runs inline until later units migrate it.
    """

    def __init__(self, *, cache_dir: Path, session_id: str) -> None:
        self.cache_dir = cache_dir
        self.session_id = session_id

    def route(
        self,
        portal: Any,
        slot_spec: Any,
        *,
        force_regenerate: bool = False,
        sketch_library: Optional[Any] = None,
    ) -> Any:
        """Routing stage: decide REUSE / INSTANTIATE / ADAPT / GENERATE.

        Returns the live ``ResolutionResult`` (the spine consumes its ``slot`` and
        lineage). Behavior is identical to calling ``resolve()`` directly -- this
        method only names the stage so the orchestrator owns the decision point.
        Use :meth:`version_context` for the JSON-safe typed projection.
        """
        from semipy.resolver import resolve

        return resolve(
            portal,
            slot_spec,
            force_regenerate=force_regenerate,
            sketch_library=sketch_library,
        )

    @staticmethod
    def version_context(resolution: Any) -> VersionContext:
        """Project a routing result into the typed ``VersionContext`` artifact."""
        return version_checker.project(resolution)
