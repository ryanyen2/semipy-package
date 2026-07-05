"""Frontier-kernel Phase 6: the routing priority cascade, relocated to
``kernel.policy.decide_route`` as one canonical pure function so
``routing.RoutingPolicy`` is a thin I/O adapter over it. Exercises the same
10-case precedence ``RoutingPolicy`` documented, with no portal/slot/LLM
required -- this had no direct unit coverage before (only reachable via
``execute_slot``, which needs an API key).
"""
from __future__ import annotations

from semipy.kernel.policy import RouteDecision, decide_route
from semipy.types import Decision


def test_no_slot_generates():
    r = decide_route(has_slot=False)
    assert r == RouteDecision(Decision.GENERATE, "none")


def test_version_lock_wins_over_everything_else():
    r = decide_route(
        has_slot=True, is_locked=True, force_regenerate=True,
        has_head=True, has_commits=True, equiv_ok=False,
    )
    assert r == RouteDecision(Decision.REUSE, "locked")


def test_force_regenerate_adapts_from_head_when_present():
    r = decide_route(has_slot=True, force_regenerate=True, has_head=True, has_commits=True)
    assert r == RouteDecision(Decision.ADAPT, "head")


def test_force_regenerate_falls_back_to_donor_when_no_head():
    r = decide_route(has_slot=True, force_regenerate=True, has_head=False, donor_available=True)
    assert r == RouteDecision(Decision.ADAPT, "donor")


def test_force_regenerate_generates_when_no_head_and_no_donor():
    r = decide_route(has_slot=True, force_regenerate=True, has_head=False, donor_available=False)
    assert r == RouteDecision(Decision.GENERATE, "none")


def test_adapt_forcing_failure_kind_adapts_from_head():
    r = decide_route(
        has_slot=True, has_head=True, has_commits=True, equiv_ok=True,
        prior_validation_failure_kind="type_mismatch",
    )
    assert r == RouteDecision(Decision.ADAPT, "head")


def test_non_adapt_forcing_failure_kind_falls_through_to_reuse():
    # execution_error/syntax_error retry via force_regenerate, not this gate.
    r = decide_route(
        has_slot=True, has_head=True, has_commits=True, equiv_ok=True,
        prior_validation_failure_kind="execution_error",
    )
    assert r == RouteDecision(Decision.REUSE, "head")


def test_adapt_forcing_failure_with_no_head_generates():
    r = decide_route(has_slot=True, has_head=False, prior_validation_failure_kind="empty_output")
    assert r == RouteDecision(Decision.GENERATE, "none")


def test_semantic_recheck_requests_adapt():
    r = decide_route(has_slot=True, has_head=True, has_commits=True, equiv_ok=True, semantic_wants_adapt=True)
    assert r == RouteDecision(Decision.ADAPT, "head")


def test_semantic_recheck_requests_adapt_with_no_head_generates():
    r = decide_route(has_slot=True, has_head=False, semantic_wants_adapt=True)
    assert r == RouteDecision(Decision.GENERATE, "none")


def test_equivalence_mismatch_instantiates_sketch_when_available():
    r = decide_route(has_slot=True, has_commits=True, equiv_ok=False, sketch_available=True)
    assert r == RouteDecision(Decision.INSTANTIATE, "sketch")


def test_equivalence_mismatch_adapts_when_no_sketch():
    r = decide_route(has_slot=True, has_commits=True, equiv_ok=False, has_head=True, sketch_available=False)
    assert r == RouteDecision(Decision.ADAPT, "head")


def test_equivalence_ok_reuses_head():
    r = decide_route(has_slot=True, has_commits=True, equiv_ok=True, has_head=True)
    assert r == RouteDecision(Decision.REUSE, "head")


def test_no_commits_reuses_donor():
    r = decide_route(has_slot=True, has_commits=False, donor_available=True)
    assert r == RouteDecision(Decision.REUSE, "donor")


def test_no_commits_no_donor_instantiates_sketch():
    r = decide_route(has_slot=True, has_commits=False, donor_available=False, sketch_available=True)
    assert r == RouteDecision(Decision.INSTANTIATE, "sketch")


def test_no_commits_no_donor_no_sketch_generates():
    r = decide_route(has_slot=True, has_commits=False, donor_available=False, sketch_available=False)
    assert r == RouteDecision(Decision.GENERATE, "none")


def test_priority_force_regenerate_beats_equivalence_ok():
    # Even a perfectly reusable head is bypassed when force_regenerate is set.
    r = decide_route(has_slot=True, force_regenerate=True, has_head=True, has_commits=True, equiv_ok=True)
    assert r == RouteDecision(Decision.ADAPT, "head")


def test_priority_prior_validation_beats_equivalence_ok():
    r = decide_route(
        has_slot=True, has_head=True, has_commits=True, equiv_ok=True,
        prior_validation_failure_kind="identity_return",
    )
    assert r.decision == Decision.ADAPT
