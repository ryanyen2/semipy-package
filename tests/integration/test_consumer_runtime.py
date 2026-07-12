"""U6/KTD-7: the consumer runtime -- resolving calls against installed package
data (``semipy build``) before any cache dir or portal, with no API key
needed for in-scope calls.

Covers scenarios 1-4 (5-7 live in tests/unit/test_build_distill.py, which also
documents the shared build shape these scenarios depend on):
  1. keyless in-scope call runs.
  2. out-of-scope adaptive call, no key -> verify-pass -> deopt-unadapted
     warning, runs anyway.
  3. out-of-scope adaptive call, no key -> verify-fail -> ``ScopeViolation``
     naming the violated conjunct.
  4. out-of-scope frozen-mode call, *with* a key -> still the verify gate
     (never the keyed/adaptive fallthrough) -> raises without an adaptation
     attempt (contrasted against adaptive+key, which does fall through).

Plus the ``slot_resolver.execute_slot`` wiring point itself (in-process) and a
keyless-subprocess run of the same wiring point, per the task's verification
requirement.

Free-variable/scope note: a scope predicate can only meaningfully violate
against list/Series/DataFrame-shaped inputs -- ``compute_input_profile``'s
scalar branch carries no ``range``/``len`` key, and ``ScopeConjunct.evaluate``
treats a missing profile key as "no evidence, not violated" by design. So
these fixtures use a list-typed free variable (``xs``) with a ``"length"``
conjunct, not a scalar with a ``"range"`` conjunct.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from semipy.contract.access import save_contract
from semipy.contract.models import ContractCase, SlotContract
from semipy.distribution.build import build_package_data
from semipy.distribution.runtime import DeoptUnadaptedWarning, ScopeViolation, try_resolve
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

# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/unit/test_build_distill.py's style)
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
) -> Slot:
    slot = Slot(slot_id=slot_id, call_site_info={}, function_name_base="f")
    slot.slot_spec = {"spec_equivalence_key": spec_equivalence_key, "spec_text": "len of xs"}
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


def _build(tmp_path: Path, slot: Slot, package_name: str = "fixturepkg") -> Path:
    """Lay out ``<tmp_path>/<package_name>/mod.py`` with a built
    ``_semiformal/`` next to it -- mirrors how an installed library ships
    package data alongside its modules. Returns ``mod.py``'s path (the call
    site's ``source_file``, per ``find_package_root``)."""
    package_dir = tmp_path / package_name
    package_dir.mkdir()
    mod_path = package_dir / "mod.py"
    mod_path.write_text("# fixture module\n", encoding="utf-8")
    build_package_data(_portal(slot), package_dir / "_semiformal")
    return mod_path


def _set_mode(mod_path: Path, mode: str) -> None:
    """Hand-patch the manifest's ``mode`` field: U7 (slot distribution modes
    as a first-class authored concept) has not landed, so ``build.py`` always
    emits ``"adaptive"``. This patches the placeholder field the resolver
    already understands (KTD-7) rather than inventing a second mechanism U7
    will have to reconcile."""
    manifest_path = mod_path.parent / "_semiformal" / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in data["entries"].values():
        entry["mode"] = mode
    manifest_path.write_text(json.dumps(data), encoding="utf-8")


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


NO_KEY = SimpleNamespace(openai_api_key=None)
WITH_KEY = SimpleNamespace(openai_api_key="sk-fake")


# ---------------------------------------------------------------------------
# Scenario 1: keyless in-scope call runs.
# ---------------------------------------------------------------------------


def test_keyless_in_scope_call_runs(tmp_path):
    slot = _slot()
    mod_path = _build(tmp_path, slot)
    result = try_resolve(_slot_spec(), {"xs": [1, 2]}, str(mod_path), NO_KEY)
    assert result == 2


def test_in_scope_call_never_constructs_llm_agent_or_orchestrator(tmp_path, monkeypatch):
    """KTD-7's promise is about call-time behavior, not import-time: ``semipy``
    unconditionally pulls in pydantic_ai at *import* time (decorator ->
    slot_resolver -> agents.agent -> agents.generator), a pre-existing fact
    unrelated to this unit. What U6 adds is that *resolving* an in-scope call
    never constructs or invokes that machinery."""
    from semipy.agents.agent import SemiAgent
    from semipy.orchestration.orchestrator import Orchestrator

    def _boom(*_a, **_k):
        raise AssertionError("LLM agent/orchestrator machinery was touched for an in-scope call")

    monkeypatch.setattr(SemiAgent, "__init__", _boom)
    monkeypatch.setattr(Orchestrator, "__init__", _boom)

    slot = _slot()
    mod_path = _build(tmp_path, slot)
    result = try_resolve(_slot_spec(), {"xs": [1, 2]}, str(mod_path), NO_KEY)
    assert result == 2


# ---------------------------------------------------------------------------
# Scenario 2: out-of-scope adaptive slot, no key -> verify-pass -> warn + run.
# ---------------------------------------------------------------------------


def test_out_of_scope_no_key_verify_pass_warns_and_runs(tmp_path):
    slot = _slot(source=IN_SCOPE_SOURCE)  # never raises, regardless of length
    mod_path = _build(tmp_path, slot)
    with pytest.warns(DeoptUnadaptedWarning):
        result = try_resolve(_slot_spec(), {"xs": [1, 2, 3, 4]}, str(mod_path), NO_KEY)
    assert result == 4


# ---------------------------------------------------------------------------
# Scenario 3: out-of-scope adaptive slot, no key -> verify-fail -> ScopeViolation.
# ---------------------------------------------------------------------------


def test_out_of_scope_no_key_verify_fail_raises_scope_violation(tmp_path):
    slot = _slot(source=RAISES_OUT_OF_SCOPE_SOURCE)
    mod_path = _build(tmp_path, slot)
    with pytest.raises(ScopeViolation) as exc_info:
        try_resolve(_slot_spec(), {"xs": [1, 2, 3, 4]}, str(mod_path), NO_KEY)
    violation = exc_info.value
    assert violation.violated == "0 <= len(xs) <= 3"
    assert violation.violated_var == "xs"
    assert violation.bundle["violated_conjunct"] == "0 <= len(xs) <= 3"


# ---------------------------------------------------------------------------
# Scenario 4: out-of-scope frozen-mode slot, WITH a key -> still the verify
# gate (never falls through to the keyed/adaptive pipeline) -> raises without
# an adaptation attempt.
# ---------------------------------------------------------------------------


def test_adaptive_mode_with_key_falls_through_instead_of_verifying(tmp_path):
    """Contrast case: an adaptive (non-frozen) out-of-scope slot with a key
    present falls through to the normal (keyed) pipeline -- U9's floor-gated
    adapt owns that path, not U6."""
    slot = _slot(source=RAISES_OUT_OF_SCOPE_SOURCE)
    mod_path = _build(tmp_path, slot)
    from semipy.distribution.runtime import FALL_THROUGH

    result = try_resolve(_slot_spec(), {"xs": [1, 2, 3, 4]}, str(mod_path), WITH_KEY)
    assert result is FALL_THROUGH


def test_frozen_mode_with_key_still_raises_without_adaptation_attempt(tmp_path):
    slot = _slot(source=RAISES_OUT_OF_SCOPE_SOURCE)
    mod_path = _build(tmp_path, slot)
    _set_mode(mod_path, "frozen")

    with pytest.raises(ScopeViolation):
        try_resolve(_slot_spec(), {"xs": [1, 2, 3, 4]}, str(mod_path), WITH_KEY)


# ---------------------------------------------------------------------------
# Wiring: slot_resolver.execute_slot's early-return seam.
# ---------------------------------------------------------------------------


def test_execute_slot_wiring_resolves_in_scope_without_cache_dir_or_portal(tmp_path):
    from semipy.slot_resolver import execute_slot

    slot = _slot()
    mod_path = _build(tmp_path, slot)
    result = execute_slot(_slot_spec(), {"xs": [1, 2]}, str(mod_path), tmp_path / "unused-cache-dir")
    assert result == 2


# ---------------------------------------------------------------------------
# Verification: the built fixture end to end in a keyless subprocess.
# ---------------------------------------------------------------------------


def test_keyless_subprocess_runs_the_built_fixture_end_to_end(tmp_path):
    slot = _slot()
    mod_path = _build(tmp_path, slot)
    script = textwrap.dedent(
        f"""
        from pathlib import Path
        from semipy.slot_resolver import execute_slot
        from semipy.types import SlotCategory, SlotSpec

        spec = SlotSpec(
            slot_id="s1", source_span=("f.py", 1, 1), spec_text="len of xs", spec_hash="h",
            spec_equivalence_key="eq-key-1", free_variables=["xs"], control_context="",
            expected_category=SlotCategory.EXPRESSION_STANDALONE, expected_type=int,
            output_names=[], formal_constraints=[], usage_hints=[],
            enclosing_function_source="", enclosing_function_qualname="",
        )
        result = execute_slot(spec, {{"xs": [1, 2]}}, {str(mod_path)!r}, Path({str(tmp_path / "unused-cache-dir")!r}))
        assert result == 2, result
        print("OK")
        """
    )
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout
