"""U12: case lifecycle -- rot revalidation and semver enforcement (KTD-8, D3).

Fixture pattern mirrors ``tests/unit/test_build_distill.py`` (bare
``Commit``/``Slot``/``Portal`` construction, ``_with_cases`` via
``save_contract``) and ``tests/unit/test_repro_bundle.py`` (``tmp_path``
portal fixtures).
"""
from __future__ import annotations

import json

import pytest

from semipy.contract.access import get_contract, save_contract
from semipy.contract.maintainer import revalidate_slot
from semipy.contract.models import ContractCase, SlotContract
from semipy.contract.surface import ContractSurface
from semipy.distribution.build import SemverViolation, build_package_data
from semipy.documents import capture_external_provenance
from semipy.history.version_control import Commit, Portal, Slot
from semipy.kernel.operators import FreezeCertificate, FreezeEvent, append_freeze_event

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _commit(source: str = "def f(x):\n    return x + 1\n", commit_id: str = "c1") -> Commit:
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


def _slot(slot_id: str = "s1", *, spec_equivalence_key: str = "eq-key-1") -> Slot:
    slot = Slot(slot_id=slot_id, call_site_info={}, function_name_base="f")
    slot.slot_spec = {"spec_equivalence_key": spec_equivalence_key, "spec_text": "add one"}
    commit = _commit()
    slot.commits[commit.commit_id] = commit
    slot.refs["head"] = commit.commit_id
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
# Scenario 1: an active case tied to a changed fixture file marks stale.
# ---------------------------------------------------------------------------


def test_case_with_changed_source_snapshot_marks_stale_on_revalidate(tmp_path):
    fixture = tmp_path / "fixture.txt"
    fixture.write_text("original content", encoding="utf-8")
    prov = capture_external_provenance(fixture, fixture.read_text(encoding="utf-8"))

    slot = _slot()
    _with_cases(slot, {
        "c-rot": ContractCase(
            case_id="c-rot", kind="invariant", invariant="non_empty", status="active",
            source_locator=str(fixture), snapshot_fingerprint=prov.snapshot_fingerprint,
        ),
    })

    fixture.write_text("changed content", encoding="utf-8")

    result = revalidate_slot(slot, max_stale_age_s=1e9)

    assert result.marked_stale == ["c-rot"]
    assert result.retired == []
    assert get_contract(slot).cases["c-rot"].status == "stale"


# ---------------------------------------------------------------------------
# Scenario 2: a stale case past the age threshold retires with a ledger event
# and drops from the built floor.
# ---------------------------------------------------------------------------


def test_stale_case_past_age_threshold_retires_and_drops_from_built_floor(tmp_path):
    slot = _slot()
    old_case = ContractCase(
        case_id="c-rot", kind="invariant", invariant="non_empty", status="stale",
        ship=True, source_locator="/no/such/file.txt", snapshot_fingerprint="deadbeef",
    )
    old_case.updated_ts = 1.0  # sat stale long ago
    _with_cases(slot, {"c-rot": old_case})

    result = revalidate_slot(slot, max_stale_age_s=60.0)

    assert result.retired == ["c-rot"]
    assert get_contract(slot).cases["c-rot"].status == "quarantined"

    output_dir = tmp_path / "_semiformal"
    build_package_data(_portal(slot), output_dir)
    contract_data = json.loads((output_dir / "contracts" / "eq-key-1.json").read_text(encoding="utf-8"))
    assert contract_data["surface"]["cases"] == {}


# ---------------------------------------------------------------------------
# Scenario 3: a still-matching snapshot stays active.
# ---------------------------------------------------------------------------


def test_unchanged_source_snapshot_stays_active(tmp_path):
    fixture = tmp_path / "fixture.txt"
    fixture.write_text("original content", encoding="utf-8")
    prov = capture_external_provenance(fixture, fixture.read_text(encoding="utf-8"))

    slot = _slot()
    _with_cases(slot, {
        "c-rot": ContractCase(
            case_id="c-rot", kind="invariant", invariant="non_empty", status="active",
            source_locator=str(fixture), snapshot_fingerprint=prov.snapshot_fingerprint,
        ),
    })

    result = revalidate_slot(slot, max_stale_age_s=1e9)

    assert result.marked_stale == []
    assert result.retired == []
    assert get_contract(slot).cases["c-rot"].status == "active"


# ---------------------------------------------------------------------------
# Scenario 4: retiring a case a licensed certificate depended on demotes the
# certificate (flags it for re-licensing) rather than leaving it trusted.
# ---------------------------------------------------------------------------


def test_retirement_of_a_case_demotes_a_licensed_certificate(tmp_path):
    slot = _slot()
    old_case = ContractCase(
        case_id="c-rot", kind="invariant", invariant="non_empty", status="stale",
        source_locator="/no/such/file.txt", snapshot_fingerprint="deadbeef",
    )
    old_case.updated_ts = 1.0
    _with_cases(slot, {"c-rot": old_case})

    licensed_cert = FreezeCertificate(
        epsilon=0.05, delta=0.1, gamma=1.0, budget_total=10, budget_spent=5,
        held_out_pass_fraction=1.0, mdl_gain=5.0, licensed=True,
    )
    append_freeze_event(slot, FreezeEvent(certificate=licensed_cert, timestamp=1.0))
    assert ContractSurface.from_slot(slot).certified is True

    result = revalidate_slot(slot, max_stale_age_s=60.0)

    assert result.retired == ["c-rot"]
    assert result.certificate_reflagged is True
    assert ContractSurface.from_slot(slot).certified is False


# ---------------------------------------------------------------------------
# Scenario 5: a build declared as a 'patch' release over a major behavioral
# change is a hard failure (KTD-8), not just a warning.
# ---------------------------------------------------------------------------


def test_build_with_major_change_under_declared_patch_raises_semver_violation(tmp_path):
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
    with pytest.raises(SemverViolation, match="major"):
        build_package_data(_portal(slot), new_dir, previous_package_dir=old_dir, release_type="patch")

    # The manifest is still written despite the violation: per-slot artifacts
    # are already persisted by the time the violation is known, so a
    # consistent _semiformal/ with a manifest is preferable to a partial one.
    assert (new_dir / "manifest.json").exists()
