"""Frontier-kernel Phase 3: freeze -- certified posterior collapse."""
from __future__ import annotations

import math
import sys

import pytest

import semipy.decisions.discriminate as discriminate_mod
import semipy.interpreted  # noqa: F401 -- registers the real module in sys.modules
from semipy.decisions.discriminate import DiscriminationResult
from semipy.history.version_control import Slot
from semipy.kernel.operators import (
    FreezeCertificate,
    FreezeEvent,
    append_freeze_event,
    freeze,
    frozen_fraction,
    get_freeze_events,
)
from semipy.kernel.policy import (
    counterexample_budget,
    freeze_break_even,
    is_comparable_output,
    mdl_compression_gain,
)

# semipy/__init__.py does `from semipy.interpreted import ... interpreted`, which
# clobbers the `semipy.interpreted` package *attribute* with that function --
# `import semipy.interpreted as X` would silently bind X to the function, not
# the module. sys.modules is unaffected by that clobbering, so use it directly.
interpreted_mod = sys.modules["semipy.interpreted"]


# ---------------------------------------------------------------------------
# policy.py -- pure math
# ---------------------------------------------------------------------------


def test_freeze_break_even_matches_the_stated_formula():
    assert freeze_break_even(c_m=2.0, c_e=100.0, gamma_e=0.5) == pytest.approx(2.0 / (0.5 * 100.0))


def test_freeze_break_even_rejects_nonpositive_costs():
    with pytest.raises(ValueError):
        freeze_break_even(c_m=1.0, c_e=0.0, gamma_e=0.5)
    with pytest.raises(ValueError):
        freeze_break_even(c_m=1.0, c_e=1.0, gamma_e=0.0)


def test_counterexample_budget_matches_the_stated_formula():
    epsilon, delta, gamma = 0.1, 0.05, 1.0
    expected = math.ceil(math.log(delta) / math.log(1 - gamma * epsilon))
    assert counterexample_budget(epsilon, delta, gamma) == expected


def test_counterexample_budget_rejects_out_of_range_inputs():
    with pytest.raises(ValueError):
        counterexample_budget(epsilon=2.0, delta=0.05, gamma=1.0)  # gamma*epsilon >= 1
    with pytest.raises(ValueError):
        counterexample_budget(epsilon=0.1, delta=1.5, gamma=1.0)  # delta out of (0,1)


def test_mdl_compression_gain_is_negative_for_a_shape_seen_once():
    # One tiny example: a real function body is longer than the single output.
    src = "def solve(x):\n    return x * 2 + 1\n"
    assert mdl_compression_gain(src, [7]) <= 0


def test_mdl_compression_gain_is_positive_when_a_short_rule_explains_many_examples():
    src = "def solve(x):\n    return x * 2\n"
    outputs = list(range(0, 40, 2))  # 20 examples worth of raw output to encode
    assert mdl_compression_gain(src, outputs) > 0


def test_is_comparable_output_true_for_labels_and_concrete_types_false_for_free_text():
    assert is_comparable_output(expected_type=str, labels=["a", "b"]) is True
    assert is_comparable_output(expected_type=int, labels=None) is True
    assert is_comparable_output(expected_type=dict, labels=None) is True
    assert is_comparable_output(expected_type=str, labels=None) is False
    assert is_comparable_output(expected_type=None, labels=None) is False


# ---------------------------------------------------------------------------
# operators.py -- freeze()
# ---------------------------------------------------------------------------

_EXAMPLES = [((i,), i * 2) for i in range(20)]  # (args, output) pairs


def test_freeze_refuses_free_text_output_before_any_gate(monkeypatch):
    calls = []
    monkeypatch.setattr(
        interpreted_mod, "synthesize_residual_source",
        lambda *a, **k: calls.append(1) or "def solve(x):\n    return str(x)\n",
    )
    _src, event = freeze(
        instruction="summarize this", free_variables=["x"], examples=_EXAMPLES,
        expected_type=str, labels=None,
    )
    assert event.certificate.licensed is False
    assert any("≈_Y" in r for r in event.certificate.refusal_reasons)
    assert not calls  # never attempted synthesis for an incomparable output


def test_freeze_licenses_when_all_gates_pass(monkeypatch):
    small_src = "def solve(x):\n    return x*2\n"
    monkeypatch.setattr(interpreted_mod, "synthesize_residual_source", lambda *a, **k: small_src)
    monkeypatch.setattr(interpreted_mod, "validate_residual", lambda *a, **k: (True, 1.0))

    src, event = freeze(
        instruction="double it", free_variables=["x"], examples=_EXAMPLES,
        expected_type=int, labels=None, samples=1,
    )
    assert src == small_src
    cert = event.certificate
    assert cert.licensed is True
    assert cert.held_out_pass_fraction == 1.0
    assert cert.mdl_gain > 0
    assert "only one residual candidate drawn" in " ".join(cert.refusal_reasons)


def test_freeze_refuses_when_held_out_reproduction_fails(monkeypatch):
    monkeypatch.setattr(
        interpreted_mod, "synthesize_residual_source",
        lambda *a, **k: "def solve(x):\n    return x*2\n",
    )
    monkeypatch.setattr(interpreted_mod, "validate_residual", lambda *a, **k: (False, 0.5))

    src, event = freeze(
        instruction="double it", free_variables=["x"], examples=_EXAMPLES,
        expected_type=int, samples=1,
    )
    assert src is None
    assert event.certificate.licensed is False
    assert any("held-out" in r for r in event.certificate.refusal_reasons)


def test_freeze_refuses_on_mdl_gate_for_a_shape_seen_once(monkeypatch):
    long_src = "def solve(x):\n    # a real implementation, not a memorized constant\n    return x * 2 + 0\n"
    monkeypatch.setattr(interpreted_mod, "synthesize_residual_source", lambda *a, **k: long_src)
    monkeypatch.setattr(interpreted_mod, "validate_residual", lambda *a, **k: (True, 1.0))

    src, event = freeze(
        instruction="double it", free_variables=["x"], examples=[((1,), 2)],
        expected_type=int, samples=1,
    )
    assert src is None
    assert event.certificate.licensed is False
    assert any("MDL gate" in r for r in event.certificate.refusal_reasons)


def test_freeze_refuses_when_counterexample_search_finds_disagreement(monkeypatch):
    sources = iter(["def solve(x):\n    return x*2\n", "def solve(x):\n    return x+x\n"])
    monkeypatch.setattr(interpreted_mod, "synthesize_residual_source", lambda *a, **k: next(sources))
    monkeypatch.setattr(interpreted_mod, "validate_residual", lambda *a, **k: (True, 1.0))
    monkeypatch.setattr(
        discriminate_mod, "search_discriminating_inputs",
        lambda *a, **k: DiscriminationResult(found=True, base_clusters=1, best_clusters=2, germ="null", tried=3),
    )

    src, event = freeze(
        instruction="double it", free_variables=["x"], examples=_EXAMPLES,
        expected_type=int, samples=2,
    )
    assert src is None
    cert = event.certificate
    assert cert.licensed is False
    assert cert.budget_spent == 3
    assert any("disagreement" in r for r in cert.refusal_reasons)


def test_freeze_refuses_when_no_residual_compiles(monkeypatch):
    monkeypatch.setattr(interpreted_mod, "synthesize_residual_source", lambda *a, **k: None)
    src, event = freeze(
        instruction="double it", free_variables=["x"], examples=_EXAMPLES,
        expected_type=int, samples=2,
    )
    assert src is None
    assert event.certificate.licensed is False
    assert "no residual candidate compiled" in event.certificate.refusal_reasons


# ---------------------------------------------------------------------------
# freeze-event ledger accessors
# ---------------------------------------------------------------------------


def test_append_and_get_freeze_events_round_trip():
    slot = Slot(slot_id="s1", call_site_info={}, function_name_base="f")
    cert = FreezeCertificate(
        epsilon=0.1, delta=0.05, gamma=1.0, budget_total=10, budget_spent=0,
        held_out_pass_fraction=1.0, mdl_gain=5.0, licensed=True,
    )
    append_freeze_event(slot, FreezeEvent(certificate=cert, node_id="n1", source_len=20))
    events = get_freeze_events(slot)
    assert len(events) == 1
    assert events[0].node_id == "n1"
    assert events[0].certificate.licensed is True


def test_frozen_fraction_reflects_most_recent_event():
    slot = Slot(slot_id="s2", call_site_info={}, function_name_base="f")
    assert frozen_fraction(slot) == 0.0  # never attempted -> molten by default

    licensed_cert = FreezeCertificate(
        epsilon=0.1, delta=0.05, gamma=1.0, budget_total=1, budget_spent=0,
        held_out_pass_fraction=1.0, mdl_gain=1.0, licensed=True,
    )
    append_freeze_event(slot, FreezeEvent(certificate=licensed_cert))
    assert frozen_fraction(slot) == 1.0

    refused_cert = FreezeCertificate(
        epsilon=0.1, delta=0.05, gamma=1.0, budget_total=1, budget_spent=0,
        held_out_pass_fraction=0.4, mdl_gain=0.0, licensed=False,
        refusal_reasons=["held-out reproducibility failed"],
    )
    append_freeze_event(slot, FreezeEvent(certificate=refused_cert))
    assert frozen_fraction(slot) == 0.0  # most recent attempt was refused
