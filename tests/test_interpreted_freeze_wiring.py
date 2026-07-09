"""Frontier-kernel Phase 3 (live wiring): _execute_interpreted_slot calls
kernel.operators.freeze -- not interpreted.attempt_promotion -- and persists
every attempt on slot.freeze_events.

Offline: monkeypatches interpret_call and kernel.operators.freeze so no LLM /
sandbox is used.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import semipy.slot_resolver as sr
import semipy.interpreted  # noqa: F401 -- registers the real module in sys.modules
from semipy.history.version_control import Portal, Slot
from semipy.kernel.operators import FreezeCertificate, FreezeEvent
from semipy.store import save_portal

# semipy/__init__.py does `from semipy.interpreted import ... interpreted`, which
# clobbers the `semipy.interpreted` package *attribute* with that function, so a
# dotted monkeypatch string ("semipy.interpreted.interpret_call") resolves the
# wrong object. sys.modules is unaffected -- patch the real module directly.
_interpreted_mod = sys.modules["semipy.interpreted"]


def _slot_spec():
    return SimpleNamespace(
        slot_id="t.classify",
        spec_text="classify the sentiment",
        free_variables=["text"],
        output_names=None,
        expected_type=str,
        source_span=("f.py", 1, 1),
        enclosing_function_qualname="f",
    )


def _seeded_examples(n: int) -> list[dict]:
    return [{"args": [f"row{i}"], "output": "positive"} for i in range(n)]


def test_execute_interpreted_slot_calls_freeze_and_records_the_event(monkeypatch, tmp_path):
    monkeypatch.setattr(_interpreted_mod, "interpret_call", lambda *a, **k: "positive")

    refusal_cert = FreezeCertificate(
        epsilon=0.05, delta=0.1, gamma=1.0, budget_total=1, budget_spent=0,
        held_out_pass_fraction=0.5, mdl_gain=0.0, licensed=False,
        refusal_reasons=["held-out reproducibility failed (0.50 < 1.00)"],
    )
    freeze_calls = []

    def _fake_freeze(**kwargs):
        freeze_calls.append(kwargs)
        return None, FreezeEvent(certificate=refusal_cert, node_id=kwargs.get("node_id", ""))

    monkeypatch.setattr("semipy.kernel.operators.freeze", _fake_freeze)

    slot_spec = _slot_spec()
    slot = Slot(slot_id=slot_spec.slot_id, call_site_info={}, function_name_base="classify")
    slot.advisor_state = {"interpreted_examples": _seeded_examples(6), "interpreted_memo": {}}
    portal = Portal(session_id="s1", source_file="f.py", module_name="f")
    portal.slots[slot_spec.slot_id] = slot
    save_portal(tmp_path, portal)

    config = SimpleNamespace(verbose=False, gist_timeout=30, e2b_api_key=None)

    sr._execute_interpreted_slot(
        slot_spec=slot_spec,
        runtime_values={"text": "new row"},
        slot=slot,
        portal=portal,
        dep_graph=None,
        current_slot_ref=None,
        cache_dir=tmp_path,
        session_id="s1",
        module_name="f",
        config=config,
        promote_after=6,
    )

    assert len(freeze_calls) == 1
    assert freeze_calls[0]["node_id"] == slot_spec.slot_id
    # ε is derived from the cost model, not hardcoded; the default costs give 0.05.
    assert freeze_calls[0]["epsilon"] == 0.05
    assert len(slot.freeze_events) == 1
    assert slot.freeze_events[0]["certificate"]["licensed"] is False
    assert slot.advisor_state["interpreted_holdout_match"] == 0.5


def test_execute_interpreted_slot_derives_freeze_epsilon_from_the_cost_model(monkeypatch, tmp_path):
    # The freeze threshold is ε* = c_m / (γ_e · c_e); configured costs must flow
    # through to the freeze call, not be ignored in favour of the static default.
    monkeypatch.setattr(_interpreted_mod, "interpret_call", lambda *a, **k: "positive")
    freeze_calls = []

    def _fake_freeze(**kwargs):
        freeze_calls.append(kwargs)
        cert = FreezeCertificate(
            epsilon=kwargs.get("epsilon", 0.0), delta=0.1, gamma=1.0, budget_total=0,
            budget_spent=0, held_out_pass_fraction=0.0, mdl_gain=0.0, licensed=False,
        )
        return None, FreezeEvent(certificate=cert, node_id=kwargs.get("node_id", ""))

    monkeypatch.setattr("semipy.kernel.operators.freeze", _fake_freeze)

    slot_spec = _slot_spec()
    slot = Slot(slot_id=slot_spec.slot_id, call_site_info={}, function_name_base="classify")
    slot.advisor_state = {"interpreted_examples": _seeded_examples(6), "interpreted_memo": {}}
    portal = Portal(session_id="s1", source_file="f.py", module_name="f")
    portal.slots[slot_spec.slot_id] = slot
    save_portal(tmp_path, portal)

    # c_m=2, c_e=20, γ_e=1 -> ε* = 2 / (1 · 20) = 0.1
    config = SimpleNamespace(
        verbose=False, gist_timeout=30, e2b_api_key=None,
        freeze_cost_molten=2.0, freeze_cost_error=20.0, freeze_gamma_e=1.0,
    )
    sr._execute_interpreted_slot(
        slot_spec=slot_spec, runtime_values={"text": "new row"}, slot=slot, portal=portal,
        dep_graph=None, current_slot_ref=None, cache_dir=tmp_path, session_id="s1",
        module_name="f", config=config, promote_after=6,
    )

    assert freeze_calls[0]["epsilon"] == 0.1
