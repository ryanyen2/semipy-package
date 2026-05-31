"""Unit tests for the effects subsystem (Stage 0: representation + plumbing).

Covers the reified effect data model, the ``fx`` capability, the in-memory
artifact backend round-trip (stage -> diff -> compensate), the ``extra_kwargs``
binding plumbing, and the validator / runtime integration that makes an
effectful slot return an :class:`EffectResult` while leaving pure slots intact.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from semipy.agents.config import configure
from semipy.agents.slot_call import bind_slot_arguments, invoke_slot
from semipy.agents.validator import validate
from semipy.effects import (
    Effect,
    EffectRecorder,
    EffectResult,
    EffectScript,
    MemoryArtifactBackend,
    register_artifact_backend,
    resolve_backend,
    unregister_artifact_backend,
)
from semipy.effects.models import compute_effect_id
from semipy.slot_resolver import _call_generated_fn
from semipy.types import GenerationSpec, SemiCallSite, SlotCategory, SlotSpec


@pytest.fixture
def effects_on():
    configure(effects_enabled=True)
    yield
    configure(effects_enabled=False)


def _slot(free_vars, category=SlotCategory.EXPRESSION_STANDALONE):
    return SlotSpec(
        slot_id="slot-demo",
        source_span=("demo.py", 1, 1),
        spec_text="demo",
        spec_hash="h",
        spec_equivalence_key="k",
        free_variables=list(free_vars),
        control_context="",
        expected_category=category,
        expected_type=type(None),
        output_names=[],
        formal_constraints=[],
        usage_hints=[],
        enclosing_function_source="",
        enclosing_function_qualname="demo",
    )


# --- model ----------------------------------------------------------------
def test_effect_id_is_content_addressed():
    a = compute_effect_id(op="update", target="db://t", payload={"x": 1}, selector={"id": 2})
    b = compute_effect_id(op="update", target="db://t", payload={"x": 1}, selector={"id": 2})
    c = compute_effect_id(op="delete", target="db://t", payload={"x": 1}, selector={"id": 2})
    assert a == b
    assert a != c


def test_effect_script_summary_targets_opcounts():
    script = EffectScript(
        effects=[
            Effect(op="update", target="mem://c", payload={"t": "g"}, selector={"id": 1}),
            Effect(op="append", target="mem://h", payload={"id": 1}),
            Effect(op="read", target="mem://c", selector={"id": 1}),
        ]
    )
    assert script.targets() == {"mem://c", "mem://h"}
    assert script.op_counts() == {"update": 1, "append": 1, "read": 1}
    assert len(script.mutating()) == 2  # read is not mutating
    assert not script.is_empty()


def test_recorder_records_ops():
    fx = EffectRecorder(provenance={"slot_id": "s"})
    fx.update("mem://c", payload={"t": "g"}, selector={"id": 1})
    fx.append("mem://h", payload={"id": 1})
    assert fx.script.op_counts() == {"update": 1, "append": 1}
    assert fx.script.effects[0].provenance["slot_id"] == "s"


# --- memory backend round-trip --------------------------------------------
def test_memory_backend_stage_diff_compensate_roundtrip():
    backend = MemoryArtifactBackend(
        stores={"customers": {42: {"id": 42, "name": "Acme", "tier": "silver"}}}
    )
    eff = Effect(op="update", target="mem://customers",
                 payload={"tier": "gold"}, selector={"id": 42})
    shadow = backend.open_shadow(eff.target)
    before = backend.snapshot(shadow)
    comp = backend.compensation_for(shadow, eff)  # capture pre-image BEFORE apply
    backend.apply(shadow, eff)
    after = backend.snapshot(shadow)
    delta = backend.diff(before, after)

    assert delta.affected_count() == 1
    assert delta.modified == [42]
    assert comp is not None

    backend.commit(shadow)
    assert backend.stores["customers"][42]["tier"] == "gold"

    # revert via the materialized compensation restores the pre-image exactly
    rshadow = backend.open_shadow(comp.target)
    backend.apply(rshadow, comp)
    backend.commit(rshadow)
    assert backend.stores["customers"][42]["tier"] == "silver"


def test_resolve_backend_by_scheme():
    backend = MemoryArtifactBackend(stores={})
    register_artifact_backend("mem", backend)
    try:
        assert resolve_backend("mem://anything") is backend
        with pytest.raises(KeyError):
            resolve_backend("db://x")
    finally:
        unregister_artifact_backend("mem")


# --- binding plumbing ------------------------------------------------------
def test_bind_extra_kwargs_filtered_by_signature():
    def effectful(customer, fx):
        return (customer, fx)

    def pure(customer):
        return customer

    rec = EffectRecorder()
    # effectful fn receives fx
    out = invoke_slot(effectful, ["customer"], ({"id": 1},), extra_kwargs={"fx": rec})
    assert out[1] is rec
    # pure fn silently ignores the injected fx (no fx parameter)
    out2 = invoke_slot(pure, ["customer"], ({"id": 1},), extra_kwargs={"fx": rec})
    assert out2 == {"id": 1}


def test_bind_positional_fallback_with_fx():
    # free-variable names (v0) don't match the fn's descriptive params -> positional
    def effectful(customer, fx):
        fx.update("mem://c", payload={"x": 1}, selector={"id": customer["id"]})
        return fx.script

    rec = EffectRecorder()
    bound = bind_slot_arguments(effectful, ["v0"], ({"id": 9},), extra_kwargs={"fx": rec})
    effectful(*bound.args, **bound.kwargs)
    assert rec.script.targets() == {"mem://c"}


# --- validator + runtime integration --------------------------------------
EFFECTFUL_SRC = (
    "def upsert(customer, fx):\n"
    "    fx.update('mem://customers', payload={'tier': customer['tier']}, "
    "selector={'id': customer['id']})\n"
    "    fx.append('mem://customers_history', payload={'id': customer['id']})\n"
    "    return fx.script\n"
)
PURE_SRC = "def expand(city):\n    return {'NYC': 'New York City'}.get(city, city)\n"


def test_validate_effectful_passes(effects_on):
    slot = _slot(["customer"])
    customer = {"id": 42, "tier": "gold"}
    spec = GenerationSpec(
        prompt="upsert", call_site=SemiCallSite("d.py", 1, "d"),
        expected_type=type(None), slot_spec=slot,
        sample_input={"args": (customer,), "kwargs": {}, "runtime_values": {"customer": customer}},
    )
    vr = validate(EFFECTFUL_SRC, expected_type=type(None), sample_input=spec.sample_input, spec=spec)
    assert vr.passed, vr.error_message


def test_call_generated_fn_effectful_returns_effect_result(effects_on):
    slot = _slot(["customer"])
    customer = {"id": 42, "name": "Acme Corp", "tier": "gold"}
    ns: dict = {}
    exec(compile(EFFECTFUL_SRC, "<t>", "exec"), ns)
    result = _call_generated_fn(
        fn=ns["upsert"], slot_spec=slot, runtime_values={"customer": customer},
        prompt_preview="upsert", generated_path="", cache_dir=Path(".semiformal"),
    )
    assert isinstance(result, EffectResult)
    assert not result.applied  # Stage 0 dry-run
    assert result.effect_script.op_counts() == {"update": 1, "append": 1}
    assert customer["tier"] == "gold"  # input dict not mutated by recording


def test_pure_slot_unchanged_when_effects_enabled(effects_on):
    slot = _slot(["city"])
    spec = GenerationSpec(
        prompt="expand", call_site=SemiCallSite("d.py", 1, "d"),
        expected_type=str, slot_spec=slot,
        sample_input={"args": ("NYC",), "kwargs": {}, "runtime_values": {"city": "NYC"}},
    )
    vr = validate(PURE_SRC, expected_type=str, sample_input=spec.sample_input, spec=spec)
    assert vr.passed
    ns: dict = {}
    exec(compile(PURE_SRC, "<t>", "exec"), ns)
    out = _call_generated_fn(
        fn=ns["expand"], slot_spec=slot, runtime_values={"city": "NYC"},
        prompt_preview="expand", generated_path="", cache_dir=Path(".semiformal"),
    )
    assert out == "New York City"
    assert not isinstance(out, EffectResult)
