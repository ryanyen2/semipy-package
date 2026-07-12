"""U6: ``semipy build`` -- distilling a portal into consumer package data.

Covers scenarios 5 (baseline hash stability), 6 (ship=false absence), and 7
(behavioral-semver classification + release-type warning), plus the
artifact/contract shape the runtime-side scenarios (1-4, in
tests/integration/test_consumer_runtime.py) depend on.
"""
from __future__ import annotations

import json

from semipy.contract.access import save_contract
from semipy.contract.models import ContractCase, SlotContract
from semipy.distribution.build import build_package_data
from semipy.history.version_control import Commit, Portal, Slot
from semipy.kernel.guard import ScopeConjunct, ScopePredicate

# ---------------------------------------------------------------------------
# Fixtures
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
    source: str = "def f(x):\n    return x + 1\n",
    commit_id: str = "c1",
    scope: ScopePredicate | None = None,
) -> Slot:
    slot = Slot(slot_id=slot_id, call_site_info={}, function_name_base="f")
    slot.slot_spec = {"spec_equivalence_key": spec_equivalence_key, "spec_text": "add one"}
    commit = _commit(source, commit_id)
    slot.commits[commit.commit_id] = commit
    slot.refs["head"] = commit.commit_id
    if scope is not None:
        slot.advisor_state["scope_predicates"] = {commit_id: scope.to_dict()}
    return slot


def _portal(*slots: Slot) -> Portal:
    portal = Portal(session_id="sess", source_file="f.py", module_name="mod")
    for slot in slots:
        portal.slots[slot.slot_id] = slot
    return portal


def _with_cases(slot: Slot, cases: dict[str, ContractCase]) -> Slot:
    contract = SlotContract()
    contract.cases.update(cases)
    save_contract(slot, contract)
    return slot


# ---------------------------------------------------------------------------
# Shape: artifact module + floor-filtered contract + scope predicate
# ---------------------------------------------------------------------------


def test_build_ships_artifact_with_renamed_function_and_scope_predicate(tmp_path):
    scope = ScopePredicate((ScopeConjunct(var="x", kind="range", params={"lo": 0, "hi": 10}),))
    slot = _slot(scope=scope)
    _with_cases(slot, {
        "c1": ContractCase(case_id="c1", kind="invariant", invariant="non_empty", status="active", ship=True),
    })
    output_dir = tmp_path / "_semiformal"
    result = build_package_data(_portal(slot), output_dir)

    entry = result.manifest.entries["eq-key-1"]
    assert entry.mode == "adaptive"
    assert entry.slot_id == "s1"

    artifact_path = output_dir / entry.artifact_module
    ns: dict = {}
    exec(compile(artifact_path.read_text(encoding="utf-8"), str(artifact_path), "exec"), ns)  # noqa: S102
    fn = ns[entry.artifact_function]
    assert fn(5) == 6

    contract_data = json.loads((output_dir / entry.contract_path).read_text(encoding="utf-8"))
    assert contract_data["scope_predicate"]["conjuncts"][0]["var"] == "x"
    assert (output_dir / "manifest.json").exists()


def test_legacy_slot_without_spec_equivalence_key_is_skipped_with_warning(tmp_path):
    slot = _slot()
    slot.slot_spec = {"spec_text": "legacy, no equivalence key"}
    result = build_package_data(_portal(slot), tmp_path / "_semiformal")
    assert result.manifest.entries == {}
    assert any("spec_equivalence_key" in w.message for w in result.warnings)


# ---------------------------------------------------------------------------
# Scenario 6: a floor case with ship=false is absent from built package data
# ---------------------------------------------------------------------------


def test_ship_false_case_is_absent_from_built_contract(tmp_path):
    slot = _slot()
    _with_cases(slot, {
        "shipped": ContractCase(case_id="shipped", kind="invariant", invariant="non_empty", status="active", ship=True),
        "unshipped": ContractCase(case_id="unshipped", kind="invariant", invariant="non_empty", status="active", ship=False),
    })
    output_dir = tmp_path / "_semiformal"
    build_package_data(_portal(slot), output_dir)

    contract_data = json.loads((output_dir / "contracts" / "eq-key-1.json").read_text(encoding="utf-8"))
    case_ids = set(contract_data["surface"]["cases"].keys())
    assert case_ids == {"shipped"}


def test_ship_true_but_superseded_case_is_also_absent(tmp_path):
    """The floor is the *active* enforced contract: a ship=true case that has
    since been superseded is audit trail, not floor -- it must not ship."""
    slot = _slot()
    _with_cases(slot, {
        "old": ContractCase(case_id="old", kind="invariant", invariant="non_empty", status="superseded", ship=True),
    })
    output_dir = tmp_path / "_semiformal"
    build_package_data(_portal(slot), output_dir)

    contract_data = json.loads((output_dir / "contracts" / "eq-key-1.json").read_text(encoding="utf-8"))
    assert contract_data["surface"]["cases"] == {}


# ---------------------------------------------------------------------------
# Scenario 5: manifest baseline hash changes iff shipped content changed
# ---------------------------------------------------------------------------


def test_baseline_hash_stable_across_identical_rebuilds(tmp_path):
    slot = _slot()
    _with_cases(slot, {
        "c1": ContractCase(case_id="c1", kind="invariant", invariant="non_empty", status="active", ship=True),
    })
    r1 = build_package_data(_portal(slot), tmp_path / "build1")
    r2 = build_package_data(_portal(slot), tmp_path / "build2")
    assert r1.manifest.baseline_hash
    assert r1.manifest.baseline_hash == r2.manifest.baseline_hash


def test_baseline_hash_changes_when_shipped_artifact_source_changes(tmp_path):
    slot = _slot()
    _with_cases(slot, {
        "c1": ContractCase(case_id="c1", kind="invariant", invariant="non_empty", status="active", ship=True),
    })
    before = build_package_data(_portal(slot), tmp_path / "before")

    slot.commits["c1"] = _commit("def f(x):\n    return x + 2\n", "c1")
    after = build_package_data(_portal(slot), tmp_path / "after")
    assert after.manifest.baseline_hash != before.manifest.baseline_hash


def test_baseline_hash_unaffected_by_an_unshipped_cases_change(tmp_path):
    slot = _slot()
    # Reuse the same ``c1`` object across both builds -- constructing a fresh
    # ContractCase would pick up a new ``created_ts``/``updated_ts`` (both
    # default to ``time.time()``), which would move the hash for a reason
    # unrelated to what this test is checking.
    c1 = ContractCase(case_id="c1", kind="invariant", invariant="non_empty", status="active", ship=True)
    _with_cases(slot, {"c1": c1})
    before = build_package_data(_portal(slot), tmp_path / "before")

    # Add an unshipped case: the floor-filtered contract this slot ships is
    # byte-identical, so the baseline hash must not move.
    _with_cases(slot, {
        "c1": c1,
        "hidden": ContractCase(case_id="hidden", kind="invariant", invariant="non_empty", status="active", ship=False),
    })
    after = build_package_data(_portal(slot), tmp_path / "after")
    assert after.manifest.baseline_hash == before.manifest.baseline_hash


# ---------------------------------------------------------------------------
# Scenario 7: superseded case vs. a previous baseline classifies major and
# warns on a declared patch release (KTD-8; U12 owns enforcement).
# ---------------------------------------------------------------------------


def test_superseded_case_vs_previous_baseline_classifies_major_and_warns_on_patch(tmp_path):
    slot = _slot()
    _with_cases(slot, {
        "c1": ContractCase(case_id="c1", kind="invariant", invariant="non_empty", status="active", ship=True),
    })
    old_dir = tmp_path / "old"
    build_package_data(_portal(slot), old_dir)

    _with_cases(slot, {
        "c1": ContractCase(
            case_id="c1", kind="invariant", invariant="non_empty", status="superseded",
            ship=True, superseded_by="c2",
        ),
        "c2": ContractCase(case_id="c2", kind="invariant", invariant="non_empty", status="active", ship=True),
    })
    new_dir = tmp_path / "new"
    result = build_package_data(
        _portal(slot), new_dir, previous_package_dir=old_dir, release_type="patch",
    )

    entry = result.manifest.entries["eq-key-1"]
    assert entry.classification == "major"
    assert any("major" in w.message and "patch" in w.message for w in result.warnings)


def test_no_previous_baseline_leaves_classification_none(tmp_path):
    slot = _slot()
    _with_cases(slot, {
        "c1": ContractCase(case_id="c1", kind="invariant", invariant="non_empty", status="active", ship=True),
    })
    result = build_package_data(_portal(slot), tmp_path / "_semiformal")
    assert result.manifest.entries["eq-key-1"].classification == "none"
    assert result.warnings == []
