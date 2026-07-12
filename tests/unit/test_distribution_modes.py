"""U7 (R12/R13): slot distribution modes -- frozen/adaptive/interpreted.

Modes are authored where the slot is authored (decorator kwarg,
``semipy.decorator._resolve_slot_modes``), persist onto ``slot.slot_spec``
(``mode``), and flow into the shipped manifest (``semipy.distribution.build``)
which gates consumer-site behavior (``semipy.distribution.runtime.try_resolve``).

Covers the four scenarios from the plan:
  1. Default (unmarked) slot manifests as adaptive.
  2. Frozen slot's deopt at consumer site raises with a bundle and never
     touches the LLM generation path.
  3. An interpreted slot without a key at consumer site raises a clear
     configuration error at call time, not import time.
  4. Mode changes flip the manifest and register as at least a minor
     contract diff.
Plus the mode x key-present x in/out-of-scope matrix against the HTD
resolution flow: frozen skips the KEYQ branch entirely (deopt -> verify ->
run-or-raise); interpreted skips the scope check entirely (always molten,
key required).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from semipy.contract.access import save_contract
from semipy.contract.models import ContractCase, SlotContract
from semipy.decorator import _resolve_slot_modes
from semipy.distribution.build import build_package_data
from semipy.distribution.runtime import FALL_THROUGH, ScopeViolation, try_resolve
from semipy.history.version_control import Commit, Portal, Slot
from semipy.kernel.guard import ScopeConjunct, ScopePredicate
from semipy.types import SlotCategory, SlotSpec

IN_SCOPE_SOURCE = "def f(xs):\n    return len(xs)\n"
RAISES_OUT_OF_SCOPE_SOURCE = (
    "def f(xs):\n"
    "    if len(xs) > 3:\n"
    "        raise ValueError('too long')\n"
    "    return len(xs)\n"
)
LENGTH_SCOPE = ScopePredicate((ScopeConjunct(var="xs", kind="length", params={"lo": 0, "hi": 3}),))

NO_KEY = SimpleNamespace(openai_api_key=None)
WITH_KEY = SimpleNamespace(openai_api_key="sk-fake")

# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/unit/test_build_distill.py and
# tests/integration/test_consumer_runtime.py's style)
# ---------------------------------------------------------------------------


def _commit(source: str, commit_id: str = "c1") -> Commit:
    return Commit(
        commit_id=commit_id,
        parent_ids=(),
        generated_source=source,
        source_hash="h",
        template_fingerprint="t",
        constants_snapshot=(),
        operation_signature="op",
        prompt_snapshot="",
        timestamp=1.0,
        message="",
        decision="GENERATE",
    )


def _slot(
    slot_id: str = "s1",
    *,
    spec_equivalence_key: str = "eq-key-1",
    source: str = IN_SCOPE_SOURCE,
    scope: ScopePredicate | None = LENGTH_SCOPE,
    mode: str | None = None,
    interpreted: bool = False,
) -> Slot:
    slot = Slot(slot_id=slot_id, call_site_info={}, function_name_base="f")
    slot.slot_spec = {"spec_equivalence_key": spec_equivalence_key, "spec_text": "len of xs"}
    if mode is not None:
        slot.slot_spec["mode"] = mode
    if interpreted:
        slot.slot_spec["interpreted"] = True
    commit = _commit(source)
    slot.commits[commit.commit_id] = commit
    slot.refs["head"] = commit.commit_id
    if scope is not None:
        slot.advisor_state["scope_predicates"] = {commit.commit_id: scope.to_dict()}
    contract = SlotContract()
    contract.cases["c1"] = ContractCase(
        case_id="c1", kind="invariant", invariant="non_empty", status="active", ship=True,
    )
    save_contract(slot, contract)
    return slot


def _portal(*slots: Slot) -> Portal:
    portal = Portal(session_id="sess", source_file="f.py", module_name="mod")
    for slot in slots:
        portal.slots[slot.slot_id] = slot
    return portal


def _build(tmp_path, slot: Slot, package_name: str = "fixturepkg", **kwargs):
    """Lay out ``<tmp_path>/<package_name>/mod.py`` with a built
    ``_semiformal/`` next to it. Returns ``mod.py``'s path (the call site's
    ``source_file``, per ``find_package_root``)."""
    package_dir = tmp_path / package_name
    package_dir.mkdir()
    mod_path = package_dir / "mod.py"
    mod_path.write_text("# fixture module\n", encoding="utf-8")
    result = build_package_data(_portal(slot), package_dir / "_semiformal", **kwargs)
    return mod_path, result


def _slot_spec(spec_equivalence_key: str = "eq-key-1") -> SlotSpec:
    return SlotSpec(
        slot_id="s1",
        source_span=("f.py", 1, 1),
        spec_text="len of xs",
        spec_hash="h",
        spec_equivalence_key=spec_equivalence_key,
        free_variables=["xs"],
        control_context="",
        expected_category=SlotCategory.EXPRESSION_STANDALONE,
        expected_type=int,
        output_names=[],
        formal_constraints=[],
        usage_hints=[],
        enclosing_function_source="",
        enclosing_function_qualname="",
    )


# ---------------------------------------------------------------------------
# Decorator: mode resolution (default / blanket / per-slot override /
# interpreted-wins / invalid mode).
# ---------------------------------------------------------------------------


def _bare_spec(slot_id: str) -> SlotSpec:
    return SlotSpec(
        slot_id=slot_id,
        source_span=("f.py", 1, 1),
        spec_text="",
        spec_hash="h",
        spec_equivalence_key=f"eq-{slot_id}",
        free_variables=[],
        control_context="",
        expected_category=SlotCategory.EXPRESSION_STANDALONE,
        expected_type=int,
        output_names=[],
        formal_constraints=[],
        usage_hints=[],
        enclosing_function_source="",
        enclosing_function_qualname="",
    )


def test_no_mode_kwarg_leaves_slots_unmodified_defaulting_to_adaptive():
    specs = [_bare_spec("s1")]
    resolved = _resolve_slot_modes(specs, interpreted=False, mode=None)
    assert resolved[0].mode == "adaptive"
    assert resolved[0].interpreted is False


def test_blanket_mode_string_applies_to_every_slot():
    specs = [_bare_spec("s1"), _bare_spec("s2")]
    resolved = _resolve_slot_modes(specs, interpreted=False, mode="frozen")
    assert [s.mode for s in resolved] == ["frozen", "frozen"]


def test_per_slot_mode_override_map_defaults_absent_slots_to_adaptive():
    specs = [_bare_spec("s1"), _bare_spec("s2")]
    resolved = _resolve_slot_modes(specs, interpreted=False, mode={0: "frozen"})
    assert resolved[0].mode == "frozen"
    assert resolved[1].mode == "adaptive"


def test_interpreted_flag_wins_over_mode_kwarg():
    specs = [_bare_spec("s1")]
    resolved = _resolve_slot_modes(specs, interpreted=True, mode="frozen")
    assert resolved[0].mode == "interpreted"
    assert resolved[0].interpreted is True


def test_mode_interpreted_also_sets_the_interpreted_flag():
    specs = [_bare_spec("s1")]
    resolved = _resolve_slot_modes(specs, interpreted=False, mode="interpreted")
    assert resolved[0].mode == "interpreted"
    assert resolved[0].interpreted is True


def test_invalid_mode_raises_value_error():
    specs = [_bare_spec("s1")]
    with pytest.raises(ValueError):
        _resolve_slot_modes(specs, interpreted=False, mode="bogus")


# ---------------------------------------------------------------------------
# Scenario 1: default (unmarked) slot manifests as adaptive.
# ---------------------------------------------------------------------------


def test_scenario1_default_slot_manifests_as_adaptive(tmp_path):
    slot = _slot()  # no mode authored
    _, result = _build(tmp_path, slot)
    assert result.manifest.entries["eq-key-1"].mode == "adaptive"


def test_authored_frozen_mode_flows_into_the_manifest(tmp_path):
    slot = _slot(mode="frozen")
    _, result = _build(tmp_path, slot)
    assert result.manifest.entries["eq-key-1"].mode == "frozen"


def test_build_refuses_to_ship_an_interpreted_slot_as_anything_else(tmp_path):
    """Approach: 'interpreted' slots reuse the existing interpreted-tier flag;
    build refuses to ship an interpreted slot as anything else. Authoring
    mode="frozen" alongside an interpreted flag must not downgrade it."""
    slot = _slot(mode="frozen", interpreted=True)
    _, result = _build(tmp_path, slot)
    assert result.manifest.entries["eq-key-1"].mode == "interpreted"


# ---------------------------------------------------------------------------
# Scenario 4: mode changes flip the manifest and register as a minor diff.
# ---------------------------------------------------------------------------


def test_scenario4_mode_only_change_classifies_as_at_least_minor(tmp_path):
    slot = _slot(mode="adaptive")
    old_dir = tmp_path / "old"
    build_package_data(_portal(slot), old_dir)

    slot.slot_spec["mode"] = "frozen"
    new_dir = tmp_path / "new"
    result = build_package_data(_portal(slot), new_dir, previous_package_dir=old_dir)

    entry = result.manifest.entries["eq-key-1"]
    assert entry.mode == "frozen"
    assert entry.classification == "minor"


def test_unchanged_mode_with_unchanged_surface_stays_none(tmp_path):
    slot = _slot(mode="adaptive")
    old_dir = tmp_path / "old"
    build_package_data(_portal(slot), old_dir)

    new_dir = tmp_path / "new"
    result = build_package_data(_portal(slot), new_dir, previous_package_dir=old_dir)
    assert result.manifest.entries["eq-key-1"].classification == "none"


# ---------------------------------------------------------------------------
# Scenario 2: frozen slot's deopt at consumer site raises with a bundle and
# never touches the LLM generation path.
# ---------------------------------------------------------------------------


def test_scenario2_frozen_deopt_raises_with_bundle_and_never_generates(tmp_path, monkeypatch):
    from semipy.agents.agent import SemiAgent
    from semipy.orchestration.orchestrator import Orchestrator

    def _boom(*_a, **_k):
        raise AssertionError("LLM agent/orchestrator machinery was touched for a frozen deopt")

    monkeypatch.setattr(SemiAgent, "__init__", _boom)
    monkeypatch.setattr(Orchestrator, "__init__", _boom)

    slot = _slot(source=RAISES_OUT_OF_SCOPE_SOURCE, mode="frozen")
    mod_path, _ = _build(tmp_path, slot)

    # A key is present -- for an adaptive slot this would fall through to the
    # keyed pipeline, but frozen must skip that branch entirely (KEYQ) and go
    # straight to the verify gate, which fails here and raises.
    with pytest.raises(ScopeViolation) as exc_info:
        try_resolve(_slot_spec(), {"xs": [1, 2, 3, 4]}, str(mod_path), WITH_KEY)

    violation = exc_info.value
    assert violation.bundle["violated_conjunct"] == "0 <= len(xs) <= 3"
    assert violation.bundle["slot_id"] == "s1"
    assert "verify_error" in violation.bundle


# ---------------------------------------------------------------------------
# Scenario 3: interpreted slot without a key raises a clear configuration
# error at call time, not import time.
# ---------------------------------------------------------------------------


def test_scenario3_interpreted_without_key_raises_configuration_error_at_call_time(tmp_path):
    slot = _slot(mode="interpreted")
    mod_path, _ = _build(tmp_path, slot)

    # Building and resolving the module import path itself must not raise --
    # only calling try_resolve without a key does.
    with pytest.raises(RuntimeError, match="interpreted"):
        try_resolve(_slot_spec(), {"xs": [1, 2]}, str(mod_path), NO_KEY)


def test_interpreted_with_key_falls_through_regardless_of_scope(tmp_path):
    slot = _slot(mode="interpreted")
    mod_path, _ = _build(tmp_path, slot)
    # In-scope input; interpreted still skips the scope check entirely and
    # falls through to the normal (keyed) generation pipeline -- always molten.
    result = try_resolve(_slot_spec(), {"xs": [1, 2]}, str(mod_path), WITH_KEY)
    assert result is FALL_THROUGH


# ---------------------------------------------------------------------------
# Verification matrix: mode x key-present x in/out-of-scope against the HTD
# resolution flow.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,source,xs,config,expected",
    [
        # adaptive: in-scope always runs the shipped artifact regardless of key.
        ("adaptive", IN_SCOPE_SOURCE, [1, 2], NO_KEY, ("value", 2)),
        ("adaptive", IN_SCOPE_SOURCE, [1, 2], WITH_KEY, ("value", 2)),
        # adaptive, out-of-scope, no key: verify gate (pass -> warn+run).
        ("adaptive", IN_SCOPE_SOURCE, [1, 2, 3, 4], NO_KEY, ("value", 4)),
        # adaptive, out-of-scope, with key: KEYQ lets it fall through.
        ("adaptive", RAISES_OUT_OF_SCOPE_SOURCE, [1, 2, 3, 4], WITH_KEY, ("fall_through", None)),
        # frozen: in-scope always runs the shipped artifact regardless of key.
        ("frozen", IN_SCOPE_SOURCE, [1, 2], NO_KEY, ("value", 2)),
        ("frozen", IN_SCOPE_SOURCE, [1, 2], WITH_KEY, ("value", 2)),
        # frozen, out-of-scope: KEYQ is skipped entirely -- always the verify
        # gate, key or not.
        ("frozen", IN_SCOPE_SOURCE, [1, 2, 3, 4], NO_KEY, ("value", 4)),
        ("frozen", IN_SCOPE_SOURCE, [1, 2, 3, 4], WITH_KEY, ("value", 4)),
        ("frozen", RAISES_OUT_OF_SCOPE_SOURCE, [1, 2, 3, 4], WITH_KEY, ("raises", None)),
        # interpreted: scope is irrelevant -- only key presence matters.
        ("interpreted", IN_SCOPE_SOURCE, [1, 2], NO_KEY, ("raises", None)),
        ("interpreted", IN_SCOPE_SOURCE, [1, 2], WITH_KEY, ("fall_through", None)),
        ("interpreted", IN_SCOPE_SOURCE, [1, 2, 3, 4], NO_KEY, ("raises", None)),
        ("interpreted", IN_SCOPE_SOURCE, [1, 2, 3, 4], WITH_KEY, ("fall_through", None)),
    ],
)
def test_mode_key_scope_matrix(tmp_path, mode, source, xs, config, expected):
    slot = _slot(source=source, mode=mode)
    mod_path, _ = _build(tmp_path, slot, package_name=f"pkg_{mode}_{id(xs)}_{config.openai_api_key}")
    kind, value = expected
    if kind == "value":
        assert try_resolve(_slot_spec(), {"xs": xs}, str(mod_path), config) == value
    elif kind == "fall_through":
        assert try_resolve(_slot_spec(), {"xs": xs}, str(mod_path), config) is FALL_THROUGH
    elif kind == "raises":
        with pytest.raises((ScopeViolation, RuntimeError)):
            try_resolve(_slot_spec(), {"xs": xs}, str(mod_path), config)
