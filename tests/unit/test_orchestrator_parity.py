"""U3: Orchestrator routing seam is behavior-identical to direct resolve(). Offline.

The full end-to-end execute_slot path requires an API key and cannot run offline,
so these tests pin the *routing seam* the orchestrator now owns: Orchestrator.route
must return exactly what resolve() returns, and the typed projection must agree.
"""
from __future__ import annotations

from types import SimpleNamespace

from semipy.orchestration.orchestrator import Orchestrator
from semipy.orchestration.roles import version_checker
from semipy.resolver import resolve
from semipy.types import Decision


def _orchestrator():
    return Orchestrator(cache_dir=SimpleNamespace(), session_id="sess")


def test_route_matches_resolve_for_generate():
    portal = SimpleNamespace(slots={})
    slot_spec = SimpleNamespace(slot_id="absent")

    direct = resolve(portal, slot_spec)
    via_orch = _orchestrator().route(portal, slot_spec)

    assert via_orch.decision == direct.decision == Decision.GENERATE
    assert via_orch.commit_id == direct.commit_id
    assert via_orch.parent_commit_ids == direct.parent_commit_ids


def test_route_passes_force_regenerate_through():
    portal = SimpleNamespace(slots={})
    slot_spec = SimpleNamespace(slot_id="absent")
    # No slot present -> GENERATE regardless; this asserts the kwarg is accepted
    # and forwarded without altering the no-slot outcome.
    via_orch = _orchestrator().route(portal, slot_spec, force_regenerate=True)
    assert via_orch.decision == Decision.GENERATE


def test_version_context_projection_agrees_with_resolution():
    portal = SimpleNamespace(slots={})
    slot_spec = SimpleNamespace(slot_id="absent")

    resolution = _orchestrator().route(portal, slot_spec)
    ctx = Orchestrator.version_context(resolution)

    assert ctx.decision == "generate"
    assert ctx.commit_id == resolution.commit_id
    # Same projection the version_checker role produces.
    assert ctx == version_checker.project(resolution)
