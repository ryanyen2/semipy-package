"""U9: the floor gate (R16) -- no consumer-site candidate commits without
replay-passing the shipped floor; adaptation parents from the shipped
artifact rather than generating from scratch while a baseline exists.

Fixture pattern mirrors ``tests/integration/test_consumer_runtime.py`` (build
a real ``_semiformal/`` package via ``build_package_data``) and
``tests/unit/test_layered_portal.py`` (the consumer-side ``Slot`` whose
``call_site_info["filename"]`` points inside the shipped package).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from semipy.contract.access import save_contract
from semipy.contract.models import ContractCase, SlotContract
from semipy.distribution.build import build_package_data
from semipy.distribution.floor_gate import FloorViolation
from semipy.history.version_control import Commit, Portal, Slot
from semipy.types import CacheEntry, GenerationSpec, SemiCallSite, SlotCategory, SlotSpec

GOOD_SOURCE = "def f(xs):\n    return len(xs)\n"
BAD_SOURCE = "def f(xs):\n    raise ValueError('boom')\n"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _commit(source: str, commit_id: str = "c1") -> Commit:
    return Commit(
        commit_id=commit_id, parent_ids=(), generated_source=source, source_hash="h",
        template_fingerprint="t", constants_snapshot=(), operation_signature="op",
        prompt_snapshot="", timestamp=1.0, message="", decision="GENERATE",
    )


_DEFAULT_FLOOR_CASE = ContractCase(
    case_id="floor-1", kind="example", input_sample={"xs": [1, 2, 3]},
    expected_type="int", expected_repr="3", status="active", ship=True,
    reason="an earlier candidate raised ValueError on xs=[1, 2, 3]; pinned the fix",
)


def _library_slot(source: str = GOOD_SOURCE, cases: dict | None = None) -> Slot:
    """The library author's own slot -- what ``semipy build`` distills. By
    default carries one floor case (``ship=True``, active) pinning the fix
    for a previously bad behavior: an earlier candidate raised on
    ``xs=[1, 2, 3]``; this case pins ``f([1, 2, 3]) == 3``. Pass ``cases`` to
    ship a different case set entirely (e.g. a different function signature)."""
    slot = Slot(slot_id="s1", call_site_info={}, function_name_base="f")
    slot.slot_spec = {"spec_equivalence_key": "eq-1", "spec_text": "len of xs"}
    commit = _commit(source)
    slot.commits[commit.commit_id] = commit
    slot.refs["head"] = commit.commit_id
    contract = SlotContract()
    contract.cases.update(cases if cases is not None else {"floor-1": _DEFAULT_FLOOR_CASE})
    save_contract(slot, contract)
    return slot


def _build_baseline(output_dir: Path, source: str = GOOD_SOURCE, cases: dict | None = None) -> str:
    portal = Portal(session_id="sess", source_file="lib.py", module_name="mod")
    portal.slots["s1"] = _library_slot(source=source, cases=cases)
    result = build_package_data(portal, output_dir)
    return result.manifest.baseline_hash


def _consumer_slot(mod_path: Path) -> Slot:
    return Slot(
        slot_id="s1",
        call_site_info={"filename": str(mod_path), "lineno": 1, "func_qualname": "f"},
        function_name_base="f",
        slot_spec={"spec_equivalence_key": "eq-1", "spec_text": "len of xs", "expected_type": "<class 'int'>"},
    )


def _slot_spec(free_variables: list | None = None, expected_type: type = int) -> SlotSpec:
    return SlotSpec(
        slot_id="s1", source_span=("mod.py", 1, 1), spec_text="len of xs", spec_hash="h",
        spec_equivalence_key="eq-1", free_variables=free_variables or ["xs"], control_context="",
        expected_category=SlotCategory.EXPRESSION_STANDALONE, expected_type=expected_type,
        output_names=[], formal_constraints=[], usage_hints=[],
        enclosing_function_source="", enclosing_function_qualname="f",
    )


class _Res:
    """Minimal stand-in for ``ResolutionResult`` -- the gate only reads these
    attributes (mirrors the ``_Res`` stub in ``tests/unit/test_effects.py``)."""
    decision = None
    parent_sources = None
    parent_commit_ids = None


def _build(tmp_path: Path, source: str = GOOD_SOURCE, cases: dict | None = None) -> Path:
    package_dir = tmp_path / "fixturepkg"
    package_dir.mkdir()
    mod_path = package_dir / "mod.py"
    mod_path.write_text("# fixture module\n", encoding="utf-8")
    _build_baseline(package_dir / "_semiformal", source=source, cases=cases)
    return mod_path


# ---------------------------------------------------------------------------
# Pitfall preservation (written first, per the execution note): a floor case
# eliminated a known-bad behavior; a stubbed generator re-proposes exactly
# that behavior on every retry; the gate must refuse the commit, naming the
# case, and must never quarantine it.
# ---------------------------------------------------------------------------


def test_pitfall_preservation_refuses_commit_that_reintroduces_a_shipped_failure(tmp_path, monkeypatch):
    import semipy.slot_resolver as sr

    mod_path = _build(tmp_path)
    slot = _consumer_slot(mod_path)
    slot_spec = _slot_spec()
    call_site = SemiCallSite(str(mod_path), 1, "f")
    spec = GenerationSpec(
        prompt="len of xs", call_site=call_site, expected_type=int, slot_spec=slot_spec,
        sample_input={"args": ([1, 2, 3],), "kwargs": {}, "runtime_values": {"xs": [1, 2, 3]}},
    )

    class _FakeAgent:
        def generate(self, gspec):
            # The gate must have named the failing floor case's own reason
            # (its case_id is not literally embedded in the message, but the
            # reason it exists for is) before retrying.
            assert "pinned the fix" in (gspec.verify_failure_context or "")
            return CacheEntry(generated_source=BAD_SOURCE)

    monkeypatch.setattr(sr, "SemiAgent", _FakeAgent)

    bad_entry = CacheEntry(generated_source=BAD_SOURCE)
    with pytest.raises(FloorViolation) as exc_info:
        sr._run_floor_gate(
            slot, slot_spec, bad_entry, spec, _Res(), {"xs": [1, 2, 3]},
            sr.get_config(), call_site,
        )
    assert exc_info.value.case_id == "floor-1"
    assert exc_info.value.slot_id == slot_spec.slot_id


# ---------------------------------------------------------------------------
# Floors compose, not replace: a candidate that satisfies the shipped floor
# can still fail a distinct LOCAL (consumer-added, non-shipped) case -- that
# is the ordinary contract gate's job, running independently of the floor
# gate, over an entirely different data source (the consumer's own overlay
# contract, not the shipped package's floor).
# ---------------------------------------------------------------------------


def test_local_overlay_case_still_enforced_by_the_ordinary_contract_gate(tmp_path, monkeypatch):
    import semipy.slot_resolver as sr
    from semipy.agents.config import SemiConfig

    mod_path = _build(tmp_path)  # ships floor-1 only: f([1, 2, 3]) == 3
    slot = _consumer_slot(mod_path)
    # A local case the consumer pinned on their own overlay -- GOOD_SOURCE
    # fails it (wrong expected type/value for a different input), unrelated
    # to the shipped floor.
    local_contract = SlotContract()
    local_contract.cases["local-1"] = ContractCase(
        case_id="local-1", kind="example", input_sample={"xs": [9, 9]},
        expected_type="str", expected_repr="'99'", status="active", ship=False,
        reason="a local expectation the consumer pinned themselves",
    )
    save_contract(slot, local_contract)

    slot_spec = _slot_spec()
    call_site = SemiCallSite(str(mod_path), 1, "f")
    spec = GenerationSpec(
        prompt="len of xs", call_site=call_site, expected_type=int, slot_spec=slot_spec,
        sample_input={"args": ([1, 2, 3],), "kwargs": {}, "runtime_values": {"xs": [1, 2, 3]}},
    )

    class _FakeAgent:
        def generate(self, gspec):
            return CacheEntry(generated_source=GOOD_SOURCE)

    monkeypatch.setattr(sr, "SemiAgent", _FakeAgent)

    entry = CacheEntry(generated_source=GOOD_SOURCE)
    config = SemiConfig(contract_gate=True, contract_gate_max_retries=1)
    entry, quarantine_ids, _change = sr._run_generate_contract_gate(
        slot, slot_spec, entry, spec, _Res(), {"xs": [1, 2, 3]}, config, call_site,
    )
    assert quarantine_ids == {"local-1"}

    # The floor gate, evaluating only the shipped floor, is untouched by the
    # local case's failure and still passes -- floors compose, not replace.
    out = sr._run_floor_gate(
        slot, slot_spec, entry, spec, _Res(), {"xs": [1, 2, 3]}, sr.get_config(), call_site,
    )
    assert out is entry


# ---------------------------------------------------------------------------
# D3: a containment relation in the floor (U11's ``ContainmentRelation``, not
# wired into ``contract.runner.run_contract`` -- see ``floor_gate._run_containment_cases``)
# gates a candidate that hallucinates an output field with no basis in the
# input text.
# ---------------------------------------------------------------------------

EXTRACT_FAITHFUL_SOURCE = "def f(text):\n    return {'order_id': 'A100'}\n"
EXTRACT_HALLUCINATING_SOURCE = (
    "def f(text):\n    return {'order_id': 'A100', 'customer': 'Wile E Coyote'}\n"
)


def test_d3_containment_relation_catches_a_hallucinated_output_field(tmp_path, monkeypatch):
    import semipy.slot_resolver as sr
    from semipy.contract.relations import ContainmentRelation

    text_input = "Order #A100, total $42"
    containment_case = ContractCase(
        case_id="floor-2", kind="metamorphic", relation="containment",
        relation_param=ContainmentRelation(text_field="text").to_dict(),
        input_sample={"text": text_input}, status="active", ship=True,
        reason="a prior candidate hallucinated a field with no basis in the input text",
    )
    mod_path = _build(
        tmp_path, source=EXTRACT_FAITHFUL_SOURCE, cases={"floor-2": containment_case},
    )
    slot = _consumer_slot(mod_path)
    slot_spec = _slot_spec(free_variables=["text"], expected_type=dict)
    call_site = SemiCallSite(str(mod_path), 1, "f")
    spec = GenerationSpec(
        prompt="extract order id", call_site=call_site, expected_type=dict, slot_spec=slot_spec,
        sample_input={"args": (text_input,), "kwargs": {}, "runtime_values": {"text": text_input}},
    )

    class _FakeAgent:
        def generate(self, gspec):
            # The gate must have named the hallucinated field before retrying.
            assert "customer" in (gspec.verify_failure_context or "")
            return CacheEntry(generated_source=EXTRACT_FAITHFUL_SOURCE)

    monkeypatch.setattr(sr, "SemiAgent", _FakeAgent)

    entry = CacheEntry(generated_source=EXTRACT_HALLUCINATING_SOURCE)
    out = sr._run_floor_gate(
        slot, slot_spec, entry, spec, _Res(), {"text": text_input}, sr.get_config(), call_site,
    )
    # Regenerated to the faithful candidate after the hallucination was named.
    assert out.generated_source == EXTRACT_FAITHFUL_SOURCE


def test_d3_containment_relation_passes_a_faithful_candidate(tmp_path):
    from semipy.contract.relations import ContainmentRelation
    from semipy.distribution.floor_gate import run_floor_contract

    text_input = "Order #A100, total $42"
    containment_case = ContractCase(
        case_id="floor-2", kind="metamorphic", relation="containment",
        relation_param=ContainmentRelation(text_field="text").to_dict(),
        input_sample={"text": text_input}, status="active", ship=True,
        reason="a prior candidate hallucinated a field with no basis in the input text",
    )
    slot_spec = _slot_spec(free_variables=["text"], expected_type=dict)

    result = run_floor_contract(
        implementation_source=EXTRACT_FAITHFUL_SOURCE, slot_spec=slot_spec,
        floor_cases=[containment_case],
    )
    assert result.passed
    assert result.evaluated_case_ids == {"floor-2"}


# ---------------------------------------------------------------------------
# Effectful floor entries replay in a shadow world, never against the
# consumer's real resources.
# ---------------------------------------------------------------------------

IDEMPOTENT_EFFECT_SOURCE = (
    "def f(cid, fx):\n"
    "    fx.update('mem://counters', payload={'value': 5}, selector={'id': cid})\n"
    "    return fx.script\n"
)
NON_IDEMPOTENT_EFFECT_SOURCE = (
    "def f(cid, fx):\n"
    "    current = fx.read('mem://counters', selector={'id': cid})[0]['value']\n"
    "    fx.update('mem://counters', payload={'value': current + 1}, selector={'id': cid})\n"
    "    return fx.script\n"
)


@pytest.fixture
def mem_backend():
    from semipy.effects import MemoryArtifactBackend, register_artifact_backend, unregister_artifact_backend

    backend = MemoryArtifactBackend(stores={"counters": {1: {"id": 1, "value": 0}}})
    register_artifact_backend("mem", backend)
    yield backend
    unregister_artifact_backend("mem")


def test_effectful_floor_replays_idempotence_in_a_shadow_never_touching_the_real_backend(mem_backend):
    from semipy.distribution.floor_gate import run_floor_contract

    idempotence_case = ContractCase(
        case_id="floor-3", kind="invariant", invariant="idempotent",
        input_sample={"cid": 1}, status="active", ship=True,
        reason="a prior candidate's update was not idempotent (repeating it kept incrementing state)",
    )
    slot_spec = _slot_spec(free_variables=["cid"], expected_type=type(None))

    good = run_floor_contract(
        implementation_source=IDEMPOTENT_EFFECT_SOURCE, slot_spec=slot_spec,
        floor_cases=[idempotence_case],
    )
    assert good.passed
    # Replayed only in a shadow world -- the real backend's store was never touched.
    assert mem_backend.stores["counters"][1]["value"] == 0

    bad = run_floor_contract(
        implementation_source=NON_IDEMPOTENT_EFFECT_SOURCE, slot_spec=slot_spec,
        floor_cases=[idempotence_case],
    )
    assert not bad.passed
    assert bad.failures[0].failure_kind == "effect_nonidempotent"
    assert "not idempotent" in bad.failures[0].message
    assert mem_backend.stores["counters"][1]["value"] == 0


# ---------------------------------------------------------------------------
# Gate cost is bounded and logged: every floor-gate run reports how many
# shipped cases it replayed and how many regeneration attempts it took.
# ---------------------------------------------------------------------------


def test_floor_gate_logs_replay_count_and_attempts(tmp_path, monkeypatch):
    import semipy.slot_resolver as sr

    mod_path = _build(tmp_path)  # ships floor-1 only
    slot = _consumer_slot(mod_path)
    slot_spec = _slot_spec()
    call_site = SemiCallSite(str(mod_path), 1, "f")
    spec = GenerationSpec(
        prompt="len of xs", call_site=call_site, expected_type=int, slot_spec=slot_spec,
        sample_input={"args": ([1, 2, 3],), "kwargs": {}, "runtime_values": {"xs": [1, 2, 3]}},
    )

    logged = []
    monkeypatch.setattr(
        sr, "print_pipeline_log",
        lambda site, stage, message: logged.append((stage, message)),
    )

    entry = CacheEntry(generated_source=GOOD_SOURCE)  # already passes the floor: no retries
    config = sr.get_config()
    assert config.verbose  # the log line is gated on verbose, same as the other acceptance gates
    sr._run_floor_gate(slot, slot_spec, entry, spec, _Res(), {"xs": [1, 2, 3]}, config, call_site)

    gate_logs = [(stage, msg) for stage, msg in logged if stage == "floor_gate"]
    assert len(gate_logs) == 1
    assert "Replayed 1 shipped floor case(s) x 1 attempt(s)." in gate_logs[0][1]


# ---------------------------------------------------------------------------
# D1 walkthrough: a consumer schema triggers adaptation (bare GENERATE
# becomes ADAPT parented on the shipped artifact -- U8's lineage, made real),
# and the shipped floor's idempotence property survives into the adapted
# implementation.
# ---------------------------------------------------------------------------


def test_d1_consumer_schema_triggers_adaptation_and_shipped_idempotence_survives(tmp_path):
    from semipy.distribution.floor_gate import adapt_from_shipped_floor, installed_floor_for, run_floor_contract
    from semipy.types import Decision

    # The shipped floor pins an idempotent str-in/str-out normalization, plus
    # its own idempotence invariant case -- a pure candidate, so the ordinary
    # (non-shadow) idempotence check in contract.runner.run_contract applies.
    idempotent_case = ContractCase(
        case_id="floor-4", kind="invariant", invariant="idempotent",
        input_sample={"text": "  Hello  "}, status="active", ship=True,
        reason="a prior candidate's normalization was not idempotent on repeated application",
    )
    normalize_source = "def f(text):\n    return text.strip()\n"
    mod_path = _build(tmp_path, source=normalize_source, cases={"floor-4": idempotent_case})
    slot = _consumer_slot(mod_path)

    # The consumer calls with a different (but compatible) schema than the
    # shipped case's own sample -- a bare GENERATE resolution, no local parent.
    resolution = _Res()
    resolution.decision = Decision.GENERATE
    resolution.parent_sources = None
    baseline_version = adapt_from_shipped_floor(slot, resolution)

    assert baseline_version is not None
    assert resolution.decision == Decision.ADAPT
    # Parented on the shipped artifact -- not from-scratch generation. The
    # build step renames the top-level function (source, not identity, is
    # what's compared here), so check it's the shipped artifact rather than
    # a byte-identical copy of the pre-build source.
    shipped_source = installed_floor_for(slot).artifact_source
    assert resolution.parent_sources == [shipped_source]
    assert "text.strip()" in shipped_source

    # The adapted implementation (here, behaviorally identical to the shipped
    # one) still replay-passes the shipped idempotence property.
    slot_spec = _slot_spec(free_variables=["text"], expected_type=str)
    result = run_floor_contract(
        implementation_source=shipped_source, slot_spec=slot_spec, floor_cases=[idempotent_case],
    )
    assert result.passed
    assert result.evaluated_case_ids == {"floor-4"}
