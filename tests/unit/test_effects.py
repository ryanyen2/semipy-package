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
from semipy.types import GenerationSpec, SemiCallError, SemiCallSite, SlotCategory, SlotSpec


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


# ============================ Stage 1: shadow + verify + gate ==============
from semipy.effects.shadow import ShadowWorld, run_effectful_source  # noqa: E402
from semipy.effects.verify import verify_static  # noqa: E402


@pytest.fixture
def gate_on():
    from semipy.agents.config import configure as _cfg
    _cfg(effects_enabled=True, effect_staging=True, effect_gate=True, verbose=False)
    yield
    _cfg(effects_enabled=False, effect_staging=False, effect_gate=False, verbose=True)


@pytest.fixture
def mem_backend():
    backend = MemoryArtifactBackend(
        stores={"customers": {42: {"id": 42, "name": "Acme", "tier": "silver"}}}
    )
    register_artifact_backend("mem", backend)
    yield backend
    unregister_artifact_backend("mem")


def test_recorder_read_your_writes_and_compensation(mem_backend):
    """A recorder bound to a shadow: reads see real pre-state and earlier writes;
    each mutating effect captures a compensation."""
    world = ShadowWorld()
    fx = EffectRecorder(world=world)
    before = fx.read("mem://customers", selector={"id": 42})
    assert before == [{"id": 42, "name": "Acme", "tier": "silver"}]
    fx.update("mem://customers", payload={"tier": "gold"}, selector={"id": 42})
    after = fx.read("mem://customers", selector={"id": 42})  # read-your-writes
    assert after[0]["tier"] == "gold"
    upd = fx.script.effects[1]
    assert upd.compensation is not None  # reversible
    world.discard_all()
    assert mem_backend.stores["customers"][42]["tier"] == "silver"  # never committed


def test_verify_static_clean_update_passes(mem_backend):
    src = ("def f(customer, fx):\n"
           "    fx.update('mem://customers', payload={'tier': 'gold'}, "
           "selector={'id': customer['id']})\n    return fx.script\n")
    script, world, err = run_effectful_source(
        src, free_variables=["customer"], runtime_values={"customer": {"id": 42}},
    )
    world.discard_all()
    assert err is None
    assert verify_static(script).passed


def test_verify_static_catches_unbounded_delete(mem_backend):
    src = "def f(customer, fx):\n    fx.delete('mem://customers')\n    return fx.script\n"
    script, world, err = run_effectful_source(
        src, free_variables=["customer"], runtime_values={"customer": {"id": 42}},
    )
    world.discard_all()
    assert err is None
    vr = verify_static(script)
    assert not vr.passed
    assert vr.failures[0].failure_kind == "effect_blast_radius"


def test_verify_static_catches_irreversible_multirecord(mem_backend):
    # delete matching two records -> memory backend cannot invert with one effect
    mem_backend.stores["customers"][7] = {"id": 7, "name": "Globex", "tier": "silver"}
    src = ("def f(tier, fx):\n"
           "    fx.delete('mem://customers', selector={'tier': tier})\n    return fx.script\n")
    script, world, err = run_effectful_source(
        src, free_variables=["tier"], runtime_values={"tier": "silver"},
    )
    world.discard_all()
    assert err is None
    vr = verify_static(script)
    assert not vr.passed
    assert any(f.failure_kind == "effect_irreversible" for f in vr.failures)


BAD_SRC = "def f(customer, fx):\n    fx.delete('mem://customers')\n    return fx.script\n"
GOOD_SRC = ("def f(customer, fx):\n"
            "    fx.update('mem://customers', payload={'tier': 'gold'}, "
            "selector={'id': customer['id']})\n    return fx.script\n")


def test_generate_effect_gate_regenerates_then_accepts(gate_on, mem_backend, monkeypatch):
    """The gate rejects an unbounded-delete candidate, appends the reason to the
    failure context, regenerates (faked), and accepts the fixed candidate."""
    from semipy.types import CacheEntry
    import semipy.slot_resolver as sr

    slot = _slot(["customer"])
    spec = GenerationSpec(
        prompt="update the customer tier", call_site=SemiCallSite("d.py", 1, "d"),
        expected_type=type(None), slot_spec=slot,
        sample_input={"args": ({"id": 42},), "kwargs": {}, "runtime_values": {"customer": {"id": 42}}},
    )

    class _FakeAgent:
        def generate(self, gspec):
            # the gate must have appended the violation reason before regenerating
            assert "unbounded" in (gspec.verify_failure_context or "").lower()
            return CacheEntry(generated_source=GOOD_SRC)

    monkeypatch.setattr(sr, "SemiAgent", _FakeAgent)

    class _Res:
        decision = None
        parent_sources = None
        parent_commit_ids = None
    bad_entry = CacheEntry(generated_source=BAD_SRC)
    out = sr._run_generate_effect_gate(
        slot, slot, bad_entry, spec, _Res(), {"customer": {"id": 42}},
        sr.get_config(), SemiCallSite("d.py", 1, "d"),
    )
    assert out.generated_source == GOOD_SRC


# --- SQLite backend: same Protocol, real transactional shadow --------------
def test_sqlite_backend_shadow_rollback_and_commit(tmp_path):
    import sqlite3

    from semipy.effects import SqliteArtifactBackend

    db = str(tmp_path / "shop.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, tier TEXT)")
    con.execute("INSERT INTO customers VALUES (42, 'Acme', 'silver')")
    con.commit()
    con.close()

    backend = SqliteArtifactBackend(db)
    register_artifact_backend("db", backend)
    try:
        eff = Effect(op="update", target="db://customers",
                     payload={"tier": "gold"}, selector={"id": 42})
        # stage in a real transaction, capture compensation, then DISCARD (rollback)
        shadow = backend.open_shadow(eff.target)
        before = backend.snapshot(shadow)
        comp = backend.compensation_for(shadow, eff)
        backend.apply(shadow, eff)
        after = backend.snapshot(shadow)
        delta = backend.diff(before, after)
        assert delta.modified == [42]
        assert comp is not None and comp.op == "update"
        backend.discard(shadow)

        # rolled back: the real db is unchanged
        con = sqlite3.connect(db)
        assert con.execute("SELECT tier FROM customers WHERE id=42").fetchone()[0] == "silver"
        con.close()

        # now COMMIT the same effect for real
        shadow2 = backend.open_shadow(eff.target)
        backend.apply(shadow2, eff)
        backend.commit(shadow2)
        con = sqlite3.connect(db)
        assert con.execute("SELECT tier FROM customers WHERE id=42").fetchone()[0] == "gold"
        con.close()

        # revert via the captured compensation restores silver
        rshadow = backend.open_shadow(comp.target)
        backend.apply(rshadow, comp)
        backend.commit(rshadow)
        con = sqlite3.connect(db)
        assert con.execute("SELECT tier FROM customers WHERE id=42").fetchone()[0] == "silver"
        con.close()
    finally:
        unregister_artifact_backend("db")


def test_sqlite_through_shadowworld_and_verify(tmp_path):
    import sqlite3

    from semipy.effects import SqliteArtifactBackend

    db = str(tmp_path / "shop2.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, tier TEXT)")
    con.execute("INSERT INTO customers VALUES (42, 'Acme', 'silver')")
    con.commit()
    con.close()

    backend = SqliteArtifactBackend(db)
    register_artifact_backend("db", backend)
    try:
        src = ("def f(customer, fx):\n"
               "    old = fx.read('db://customers', selector={'id': customer['id']})\n"
               "    fx.update('db://customers', payload={'tier': customer['tier']}, "
               "selector={'id': customer['id']})\n    return fx.script\n")
        script, world, err = run_effectful_source(
            src, free_variables=["customer"],
            runtime_values={"customer": {"id": 42, "tier": "gold"}},
        )
        assert err is None
        # the read saw real pre-state through the same Protocol used by mem://
        read_eff = next(e for e in script.effects if e.op == "read")
        assert read_eff is not None
        assert verify_static(script).passed
        world.discard_all()
    finally:
        unregister_artifact_backend("db")


# ============================ Stage 2: blast-radius regression =============
from semipy.effects.diff import compute_effect_state_diff  # noqa: E402


def _script_of(src, customer):
    script, world, err = run_effectful_source(
        src, free_variables=["customer"], runtime_values={"customer": customer},
    )
    world.discard_all()
    assert err is None, err
    return script


PARENT_UPDATE = ("def f(customer, fx):\n"
                 "    fx.update('mem://customers', payload={'tier': 'gold'}, "
                 "selector={'id': customer['id']})\n    return fx.script\n")
NEW_DELETE = ("def f(customer, fx):\n"
              "    fx.delete('mem://customers', selector={'id': customer['id']})\n"
              "    return fx.script\n")


def _seed():
    b = MemoryArtifactBackend(stores={"customers": {
        42: {"id": 42, "tier": "silver"}, 7: {"id": 7, "tier": "silver"},
        9: {"id": 9, "tier": "silver"}}})
    register_artifact_backend("mem", b)
    return b


def test_regression_flags_destructive_escalation():
    _seed()
    try:
        new_script = _script_of(NEW_DELETE, {"id": 42})
        diff = compute_effect_state_diff(
            parent_source=PARENT_UPDATE, new_script=new_script,
            free_variables=["customer"], runtime_values={"customer": {"id": 42}},
        )
        assert diff.regression
        assert "more destructive" in diff.summary
    finally:
        unregister_artifact_backend("mem")


def test_no_regression_when_same_shape():
    _seed()
    try:
        new_script = _script_of(PARENT_UPDATE, {"id": 42})  # identical to parent
        diff = compute_effect_state_diff(
            parent_source=PARENT_UPDATE, new_script=new_script,
            free_variables=["customer"], runtime_values={"customer": {"id": 42}},
        )
        assert not diff.regression
    finally:
        unregister_artifact_backend("mem")


def test_regression_flags_materially_larger_update():
    _seed()
    try:
        # new updates all 3 silver rows; parent updates only id=42
        new_src = ("def f(customer, fx):\n"
                   "    fx.update('mem://customers', payload={'tier': 'gold'}, "
                   "selector={'tier': 'silver'})\n    return fx.script\n")
        new_script = _script_of(new_src, {"id": 42})
        diff = compute_effect_state_diff(
            parent_source=PARENT_UPDATE, new_script=new_script,
            free_variables=["customer"], runtime_values={"customer": {"id": 42}},
        )
        assert diff.regression
        assert "materially larger" in diff.summary
    finally:
        unregister_artifact_backend("mem")


def test_no_regression_on_fresh_generate():
    _seed()
    try:
        new_script = _script_of(NEW_DELETE, {"id": 42})
        diff = compute_effect_state_diff(
            parent_source=None, new_script=new_script,  # no parent
            free_variables=["customer"], runtime_values={"customer": {"id": 42}},
        )
        assert not diff.regression
    finally:
        unregister_artifact_backend("mem")


# ============================ Stage 3: forall-inputs proofs ================
from semipy.effects.prove import (  # noqa: E402
    prove_append_only,
    prove_bounded_blast_radius,
    prove_target_whitelist,
)


def test_memory_schema_reports_key():
    b = MemoryArtifactBackend(stores={"customers": {1: {"id": 1}}})
    sch = b.schema("mem://customers")
    assert sch.has_unique_subset({"id"})
    assert not sch.has_unique_subset({"tier"})


def test_prove_blast_radius_proved_for_key_selector():
    b = _seed()
    try:
        s = _script_of(PARENT_UPDATE, {"id": 42})  # update WHERE id=...
        from semipy.effects.backends import resolve_backend
        pr = prove_bounded_blast_radius(s, lambda t: resolve_backend(t).schema(t))
        assert pr.status == "proved"
    finally:
        unregister_artifact_backend("mem")


def test_prove_blast_radius_unknown_for_nonkey_selector():
    b = _seed()
    try:
        src = ("def f(customer, fx):\n"
               "    fx.update('mem://customers', payload={'tier': 'gold'}, "
               "selector={'tier': 'silver'})\n    return fx.script\n")
        s = _script_of(src, {"id": 42})
        from semipy.effects.backends import resolve_backend
        pr = prove_bounded_blast_radius(s, lambda t: resolve_backend(t).schema(t))
        assert pr.status == "unknown"
        assert "not a unique key" in pr.detail
        assert "id" in pr.detail  # actionable hint names the key
    finally:
        unregister_artifact_backend("mem")


def test_prove_append_only_ast():
    no_del = "def f(x, fx):\n    fx.update('db://t', payload={'a': 1}, selector={'id': x})\n"
    has_del = "def f(x, fx):\n    fx.delete('db://t', selector={'id': x})\n"
    dyn = "def f(x, fx):\n    getattr(fx, 'delete')('db://t', selector={'id': x})\n"
    assert prove_append_only(no_del).status == "proved"
    assert prove_append_only(has_del).status == "refuted"
    assert prove_append_only(dyn).status == "unknown"


def test_prove_target_whitelist_ast():
    src = ("def f(x, fx):\n"
           "    fx.update('db://customers', payload={'a': 1}, selector={'id': x})\n"
           "    fx.append('db://history', payload={'a': 1})\n")
    assert prove_target_whitelist(src, {"db://customers", "db://history"}).status == "proved"
    assert prove_target_whitelist(src, {"db://customers"}).status == "refuted"
    dyn = "def f(t, fx):\n    fx.update(t, payload={'a': 1}, selector={'id': 1})\n"
    assert prove_target_whitelist(dyn, {"db://customers"}).status == "unknown"


def test_sqlite_schema_introspects_pk_and_unique(tmp_path):
    import sqlite3
    from semipy.effects import SqliteArtifactBackend

    db = str(tmp_path / "s.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT)")
    con.commit(); con.close()
    sch = SqliteArtifactBackend(db).schema("db://customers")
    assert sch.has_unique_subset({"id"})
    assert sch.has_unique_subset({"email"})
    assert not sch.has_unique_subset({"name"})


BAD_SMT = ("def f(customer, fx):\n"
           "    fx.delete('mem://customers', selector={'tier': customer['tier']})\n"
           "    return fx.script\n")
GOOD_SMT = ("def f(customer, fx):\n"
            "    fx.delete('mem://customers', selector={'id': customer['id']})\n"
            "    return fx.script\n")


def test_effect_smt_gate_requires_key_bounded_mutation(monkeypatch):
    from semipy.agents.config import configure as _cfg
    from semipy.types import CacheEntry
    import semipy.slot_resolver as sr

    b = MemoryArtifactBackend(stores={"customers": {42: {"id": 42, "tier": "silver"}}})
    register_artifact_backend("mem", b)
    _cfg(effects_enabled=True, effect_staging=True, effect_gate=True, effect_smt=True, verbose=False)
    try:
        slot = _slot(["customer"])
        spec = GenerationSpec(
            prompt="delete the customer", call_site=SemiCallSite("d.py", 1, "d"),
            expected_type=type(None), slot_spec=slot,
            sample_input={"args": ({"id": 42, "tier": "silver"},), "kwargs": {},
                          "runtime_values": {"customer": {"id": 42, "tier": "silver"}}},
        )

        class _FakeAgent:
            def generate(self, gspec):
                assert "not provably bounded" in (gspec.verify_failure_context or "").lower()
                return CacheEntry(generated_source=GOOD_SMT)

        monkeypatch.setattr(sr, "SemiAgent", _FakeAgent)

        class _Res:
            decision = None
            parent_sources = None
            parent_commit_ids = None

        out = sr._run_generate_effect_gate(
            slot, slot, CacheEntry(generated_source=BAD_SMT), spec, _Res(),
            {"customer": {"id": 42, "tier": "silver"}}, sr.get_config(),
            SemiCallSite("d.py", 1, "d"),
        )
        assert out.generated_source == GOOD_SMT
    finally:
        unregister_artifact_backend("mem")
        _cfg(effects_enabled=False, effect_staging=False, effect_gate=False, effect_smt=False, verbose=True)


# ============================ Stage 4: ledger + apply + revert + provenance =
from types import SimpleNamespace  # noqa: E402

from semipy.effects.apply import execute_effectful  # noqa: E402
from semipy.effects.compensate import revert as revert_effects  # noqa: E402
from semipy.effects.compensate import revert_ledger_event  # noqa: E402
from semipy.effects.ledger import (  # noqa: E402
    EffectLedger, get_ledger, ledger_from_dict, ledger_to_dict,
)
from semipy.effects.models import Effect as _Effect  # noqa: E402
from semipy.effects.models import LedgerEvent  # noqa: E402
from semipy.effects.provenance import provenance_for  # noqa: E402
from semipy.history.version_control import Slot  # noqa: E402

UPSERT_SRC = ("def f(customer, fx):\n"
              "    fx.update('mem://customers', payload={'tier': customer['tier']}, "
              "selector={'id': customer['id']})\n    return fx.script\n")
MULTI_DELETE_SRC = ("def f(customer, fx):\n"
                    "    fx.delete('mem://customers', selector={'tier': 'silver'})\n"
                    "    return fx.script\n")

APPLY_CFG = SimpleNamespace(effect_staging=True, effect_gate=True,
                            effect_auto_apply=True, effect_smt=False, effects_enabled=True)


def _slot_with_commit(src):
    slot = Slot(slot_id="slot-demo", call_site_info={}, function_name_base="f")
    commit = SimpleNamespace(commit_id="c1", generated_source=src, decision="GENERATE",
                             change_record={"reason": "upsert the customer tier"},
                             prompt_snapshot="upsert the customer")
    slot.commits["c1"] = commit
    return slot, commit


def _compile(src):
    ns = {}
    exec(compile(src, "<t>", "exec"), ns)
    return ns["f"]


def test_ledger_serialization_roundtrip():
    e = _Effect(op="update", target="mem://c", payload={"tier": "gold"}, selector={"id": 1},
                compensation=_Effect(op="update", target="mem://c", payload={"tier": "silver"},
                                     selector={"id": 1}))
    ev = LedgerEvent(event_id="e1", slot_id="s", origin_commit_id="c1", invocation_id="i1",
                     applied_effects=[e], compensations=[e.compensation])
    led = EffectLedger(); led.append(ev)
    d = ledger_to_dict(led)
    back = ledger_from_dict(d)
    assert len(back.events) == 1
    ev2 = back.events[0]
    assert ev2.applied_effects[0].compensation.payload == {"tier": "silver"}
    assert ev2.applied_effects[0].op == "update"


def test_execute_effectful_auto_apply_records_ledger_and_commits():
    backend = MemoryArtifactBackend(stores={"customers": {42: {"id": 42, "tier": "silver"}}})
    register_artifact_backend("mem", backend)
    try:
        slot, commit = _slot_with_commit(UPSERT_SRC)
        res = execute_effectful(
            fn=_compile(UPSERT_SRC), slot_spec=_slot(["customer"]),
            runtime_values={"customer": {"id": 42, "tier": "gold"}}, config=APPLY_CFG,
            slot=slot, commit=commit, portal=None, cache_dir=None,
        )
        assert res.applied and res.event_id
        assert backend.stores["customers"][42]["tier"] == "gold"  # committed for real
        led = get_ledger(slot)
        assert len(led.events) == 1
        assert led.events[0].origin_commit_id == "c1"
        assert led.events[0].compensations  # materialized inverse stored

        # in-hand revert restores the store
        n = res.revert()
        assert n == 1
        assert backend.stores["customers"][42]["tier"] == "silver"
    finally:
        unregister_artifact_backend("mem")


def test_execute_effectful_refuses_irreversible():
    backend = MemoryArtifactBackend(stores={"customers": {
        42: {"id": 42, "tier": "silver"}, 7: {"id": 7, "tier": "silver"}}})
    register_artifact_backend("mem", backend)
    try:
        from semipy.effects import EffectRefused
        slot, commit = _slot_with_commit(MULTI_DELETE_SRC)
        with pytest.raises(EffectRefused) as exc:
            execute_effectful(
                fn=_compile(MULTI_DELETE_SRC), slot_spec=_slot(["customer"]),
                runtime_values={"customer": {"id": 42}}, config=APPLY_CFG,
                slot=slot, commit=commit,
            )
        # the refusal message names the reason and is not framed as a code failure
        assert "refused to apply" in str(exc.value)
        # refused -> store unchanged, nothing ledgered
        assert len(backend.stores["customers"]) == 2
        assert not get_ledger(slot).events
    finally:
        unregister_artifact_backend("mem")


def test_dry_run_when_auto_apply_off():
    backend = MemoryArtifactBackend(stores={"customers": {42: {"id": 42, "tier": "silver"}}})
    register_artifact_backend("mem", backend)
    try:
        slot, commit = _slot_with_commit(UPSERT_SRC)
        cfg = SimpleNamespace(effect_staging=True, effect_gate=True,
                              effect_auto_apply=False, effect_smt=False, effects_enabled=True)
        res = execute_effectful(
            fn=_compile(UPSERT_SRC), slot_spec=_slot(["customer"]),
            runtime_values={"customer": {"id": 42, "tier": "gold"}}, config=cfg,
            slot=slot, commit=commit,
        )
        assert not res.applied
        assert backend.stores["customers"][42]["tier"] == "silver"  # untouched
        assert not get_ledger(slot).events
    finally:
        unregister_artifact_backend("mem")


def test_durable_revert_ledger_event_and_provenance():
    backend = MemoryArtifactBackend(stores={"customers": {42: {"id": 42, "tier": "silver"}}})
    register_artifact_backend("mem", backend)
    try:
        slot, commit = _slot_with_commit(UPSERT_SRC)
        res = execute_effectful(
            fn=_compile(UPSERT_SRC), slot_spec=_slot(["customer"]),
            runtime_values={"customer": {"id": 42, "tier": "gold"}}, config=APPLY_CFG,
            slot=slot, commit=commit,
        )
        # provenance walk: event -> commit -> spec/reason
        chain = provenance_for(slot, res.event_id)
        assert chain is not None
        assert chain.origin_commit_id == "c1"
        assert "upsert" in chain.reason
        assert "mem://customers" in chain.targets

        # durable revert restores + appends a reverted event (append-only)
        n = revert_ledger_event(slot, res.event_id)
        assert n == 1
        assert backend.stores["customers"][42]["tier"] == "silver"
        led = get_ledger(slot)
        assert len(led.reverted()) >= 1
        assert led.find(res.event_id).status == "reverted"
    finally:
        unregister_artifact_backend("mem")


def test_slot_ledger_persists_through_store(tmp_path):
    from semipy.history.version_control import Portal
    from semipy.store import load_portal, save_portal

    portal = Portal(session_id="x", source_file="f.py", module_name="m")
    slot = Slot(slot_id="s", call_site_info={}, function_name_base="f")
    ev = LedgerEvent(event_id="e1", slot_id="s", origin_commit_id="c1", invocation_id="i1",
                     applied_effects=[_Effect(op="update", target="mem://c",
                                              payload={"a": 1}, selector={"id": 1})])
    led = EffectLedger(); led.append(ev)
    slot.ledger = ledger_to_dict(led)
    portal.slots["s"] = slot
    save_portal(tmp_path, portal)
    loaded = load_portal(tmp_path, "x", "f.py", "m")
    assert "s" in loaded.slots
    rl = get_ledger(loaded.slots["s"])
    assert len(rl.events) == 1
    assert rl.events[0].applied_effects[0].target == "mem://c"


# ============================ Stage 5: externalized / irreversible =========
from semipy.effects import ExternalArtifactBackend  # noqa: E402
from semipy.effects.verify import verify_static as _verify_static  # noqa: E402

NOTIFY_SRC = ("def f(user, fx):\n"
              "    fx.call('api://email', payload={'to': user['email'], "
              "'idempotency_key': user['id']})\n    return fx.script\n")


def test_external_backend_plans_then_sends_idempotently():
    sent = []
    backend = ExternalArtifactBackend(sender=lambda e: sent.append(e), scheme="api")
    eff = _Effect(op="call", target="api://email", payload={"to": "a@b.c", "idempotency_key": "u1"})
    sh = backend.open_shadow(eff.target)
    backend.apply(sh, eff)
    assert sent == []  # apply plans, does not send
    backend.commit(sh)
    assert len(sent) == 1  # commit performs it
    # idempotent: same key never performed twice
    sh2 = backend.open_shadow(eff.target)
    backend.apply(sh2, eff)
    backend.commit(sh2)
    assert len(sent) == 1


def test_verify_static_exempts_external_from_reversible():
    # a call with no compensation would normally fail reversible; external is exempt
    script = EffectScript(effects=[_Effect(op="call", target="api://email", payload={"to": "x"})])
    assert not _verify_static(script).passed  # not exempt -> irreversible fails
    assert _verify_static(script, is_external=lambda t: t.startswith("api://")).passed


def test_external_effect_requires_approval_before_send():
    sent = []
    backend = ExternalArtifactBackend(sender=lambda e: sent.append(e), scheme="api")
    register_artifact_backend("api", backend)
    try:
        slot, commit = _slot_with_commit(NOTIFY_SRC)
        # no approval callback -> external effect stays planned, never sent
        cfg_noapprove = SimpleNamespace(
            effect_staging=True, effect_gate=True, effect_auto_apply=True, effect_smt=False,
            effects_enabled=True, effect_require_approval_external=True,
            effect_approval_callback=None,
        )
        res = execute_effectful(
            fn=_compile(NOTIFY_SRC), slot_spec=_slot(["user"]),
            runtime_values={"user": {"id": "u1", "email": "a@b.c"}}, config=cfg_noapprove,
            slot=slot, commit=commit,
        )
        assert not res.applied
        assert sent == []  # refused without approval
        assert not get_ledger(slot).events

        # with approval -> performed + ledgered
        cfg_approve = SimpleNamespace(
            effect_staging=True, effect_gate=True, effect_auto_apply=True, effect_smt=False,
            effects_enabled=True, effect_require_approval_external=True,
            effect_approval_callback=lambda script: True,
        )
        res2 = execute_effectful(
            fn=_compile(NOTIFY_SRC), slot_spec=_slot(["user"]),
            runtime_values={"user": {"id": "u1", "email": "a@b.c"}}, config=cfg_approve,
            slot=slot, commit=commit,
        )
        assert res2.applied
        assert len(sent) == 1
        assert len(get_ledger(slot).events) == 1
    finally:
        unregister_artifact_backend("api")
