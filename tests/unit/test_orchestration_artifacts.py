"""U2: typed handoff artifacts + deterministic role adapters. Fully offline."""
from __future__ import annotations

import json
from types import SimpleNamespace

from semipy.orchestration.artifacts import (
    ExecutionEvidence,
    ExplorationResult,
    GenerationResult,
    ReuseVerdict,
    SurfacePlan,
    VerificationVerdict,
    VersionContext,
)
from semipy.orchestration.roles import executor_role, version_checker

ALL_ARTIFACTS = [
    ExplorationResult,
    VersionContext,
    ReuseVerdict,
    GenerationResult,
    ExecutionEvidence,
    VerificationVerdict,
    SurfacePlan,
]


# --- artifacts ------------------------------------------------------------

def test_artifacts_construct_with_representative_values():
    ex = ExplorationResult(dependency_signatures=["def f(x: int) -> str"], data_profile="n=10")
    vc = VersionContext(decision="generate", parent_commit_ids=["c1"])
    rv = ReuseVerdict(verdict="reuse", evidence_samples=[{"input": "a", "output": "A"}])
    gr = GenerationResult(generated_source="def f(): return 1", function_name="f")
    ev = ExecutionEvidence(success=True, io_pairs=[{"input": 1, "output": 2}])
    vv = VerificationVerdict(passed=False, failure_kind="empty_output", reasons=["empty"])
    sp = SurfacePlan(steering_values={"intent": "classify"}, verified="2/2 examples")
    assert ex.dependency_signatures and vc.decision == "generate"
    assert rv.verdict == "reuse" and gr.function_name == "f"
    assert ev.io_pairs and vv.failure_kind == "empty_output" and sp.verified


def test_artifacts_are_json_serializable():
    instances = [
        ExplorationResult(),
        VersionContext(decision="reuse"),
        ReuseVerdict(verdict="adapt"),
        GenerationResult(generated_source="x"),
        ExecutionEvidence(success=False, error="boom"),
        VerificationVerdict(passed=True),
        SurfacePlan(),
    ]
    for inst in instances:
        dumped = inst.model_dump_json()
        # Round-trips through json and back into the model unchanged.
        reloaded = type(inst).model_validate(json.loads(dumped))
        assert reloaded == inst


# --- version_checker.route ------------------------------------------------

def test_route_projects_generate_for_absent_slot():
    """An empty portal routes to GENERATE; the adapter projects it faithfully."""
    portal = SimpleNamespace(slots={})
    slot_spec = SimpleNamespace(slot_id="nonexistent-slot")
    ctx = version_checker.route(portal, slot_spec)
    assert isinstance(ctx, VersionContext)
    assert ctx.decision == "generate"
    assert ctx.commit_id is None
    assert ctx.parent_commit_ids == []
    # Decision is projected as the enum's string value, not the enum repr.
    assert ctx.decision == "generate" and "Decision" not in ctx.decision


# --- executor_role --------------------------------------------------------

def test_run_gist_success_parses_io_pairs():
    gist = (
        "import json\n"
        "rows = [{'input': 'hello', 'output': 'HELLO'}, {'input': 'hi', 'output': 'HI'}]\n"
        "print(json.dumps(rows))\n"
    )
    ev = executor_role.run_gist(gist)
    assert ev.success is True
    assert ev.io_pairs == [
        {"input": "hello", "output": "HELLO"},
        {"input": "hi", "output": "HI"},
    ]


def test_run_gist_captures_exception_on_raise():
    gist = "raise ValueError('candidate exploded')\n"
    ev = executor_role.run_gist(gist)
    assert ev.success is False
    assert ev.io_pairs == []
    assert ev.error and "candidate exploded" in (ev.error + ev.stdout)


def test_run_gist_success_without_io_array():
    gist = "print('just some output, no json array')\n"
    ev = executor_role.run_gist(gist)
    assert ev.success is True
    assert ev.io_pairs == []


# --- _parse_io_pairs hardening (key validation, last-array wins) -----------

def test_parse_io_pairs_rejects_array_without_io_keys():
    # An unrelated JSON array of dicts must NOT be accepted as graded evidence.
    assert executor_role._parse_io_pairs('[{"a": 1}, {"b": 2}]') == []


def test_parse_io_pairs_requires_both_keys():
    assert executor_role._parse_io_pairs('[{"input": "x"}]') == []
    assert executor_role._parse_io_pairs('[{"input": "x", "output": "X"}]') == [
        {"input": "x", "output": "X"}
    ]


def test_parse_io_pairs_prefers_last_valid_array():
    # Output (or a decoy array) then the real io array: the LAST valid one wins.
    stdout = (
        '[{"unrelated": "noise"}]\n'
        "some log line\n"
        '[{"input": "x", "output": "X"}]\n'
    )
    assert executor_role._parse_io_pairs(stdout) == [{"input": "x", "output": "X"}]
