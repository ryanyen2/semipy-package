"""U10: repro bundles and ingestion (R18).

Fixture pattern mirrors ``tests/test_decision_assert.py`` (bare in-memory
``Slot``) and ``tests/unit/test_dispute.py`` (portal-on-disk + CLI handler for
the adjudication scenario).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from semipy.cli import cmd_dispute
from semipy.contract.surface import ContractSurface
from semipy.distribution.repro import (
    IngestError,
    bundle_from_dispute,
    bundle_from_scope_violation,
    ingest_bundle,
)
from semipy.history.version_control import Commit, Portal, Slot
from semipy.store import _portal_path, load_portal, save_portal


def _commit(commit_id: str, *, baseline_version: str | None = None) -> Commit:
    return Commit(
        commit_id=commit_id, parent_ids=(), generated_source="def f(xs):\n    return len(xs)\n",
        source_hash="h", template_fingerprint="t", constants_snapshot=(),
        operation_signature="op", prompt_snapshot="", timestamp=1.0, message="",
        decision="ADAPT",
        commitment_record={"baseline_version": baseline_version} if baseline_version else {},
    )


def _developer_slot(*, spec_equivalence_key: str = "eq-1", baseline_version: str | None = None) -> Slot:
    slot = Slot(slot_id="dev.f", call_site_info={}, function_name_base="f")
    slot.slot_spec = {"spec_equivalence_key": spec_equivalence_key, "spec_text": "len of xs"}
    commit = _commit("c1", baseline_version=baseline_version)
    slot.commits[commit.commit_id] = commit
    slot.refs["head"] = commit.commit_id
    return slot


def _portal_with_slot(tmp_path: Path, slot: Slot) -> tuple[Path, str]:
    session_id = "sess1"
    portal = Portal(session_id=session_id, source_file="m.py", module_name="m", slots={slot.slot_id: slot})
    save_portal(tmp_path, portal)
    return _portal_path(tmp_path, session_id), slot.slot_id


class _FakeScopeViolation:
    """Stand-in for ``distribution.runtime.ScopeViolation`` -- only ``.bundle`` is read."""

    def __init__(self, bundle: dict) -> None:
        self.bundle = bundle


# ---------------------------------------------------------------------------
# Scenario 1: bundle from a ScopeViolation contains the violated conjunct and
# no raw secret material (reuses U3's redaction patterns on the bundle path).
# ---------------------------------------------------------------------------


def test_bundle_from_scope_violation_contains_violated_conjunct_and_redacts_secrets():
    exc = _FakeScopeViolation(
        {
            "slot_id": "consumer.slot1",
            "violated_conjunct": "len(xs) < 100",
            "violated_var": "xs",
            "observed_profile": {
                "xs": {"len": 500},
                "api_key": "not-a-real-secret-value-0123456789",
            },
            "verify_error": "assertion failed",
        }
    )
    bundle = bundle_from_scope_violation(
        exc, spec_equivalence_key="eq-1", mode="adaptive", baseline_version="base-1",
    )
    assert bundle.event_kind == "scope_violation"
    assert bundle.violated_conjunct == "len(xs) < 100"
    assert bundle.violated_var == "xs"
    # Non-secret structure survives intact.
    assert bundle.observed_profile["xs"] == {"len": 500}
    # Secret-named field is masked, not carried in the clear.
    assert bundle.observed_profile["api_key"] == "<REDACTED:api_key>"
    assert "not-a-real-secret-value-0123456789" not in str(bundle.observed_profile)


# ---------------------------------------------------------------------------
# Scenario 2: ingest of a valid bundle creates a quarantined case visible in
# the slot's contract surface (what `contract show` renders).
# ---------------------------------------------------------------------------


def test_ingest_valid_bundle_creates_quarantined_case_visible_in_contract_surface(tmp_path):
    slot = _developer_slot(baseline_version="base-1")
    _, slot_id = _portal_with_slot(tmp_path, slot)
    portal = load_portal(tmp_path, "sess1", "m.py", "m")

    bundle = bundle_from_dispute(
        spec_equivalence_key="eq-1", mode="adaptive", baseline_version="base-1",
        property_text="a fully-null site must not change other sites' averages",
        input_sample={"xs": [1, 2, 3]},
    )
    result = ingest_bundle(portal, bundle)
    save_portal(tmp_path, portal)

    reloaded_slot = load_portal(tmp_path, "sess1", "m.py", "m").slots[slot_id]
    surface = ContractSurface.from_slot(reloaded_slot)
    case = surface.cases[result.case_id]
    assert case["status"] == "quarantined"
    assert case["provenance"] == "consumer-report"
    assert case["ship"] is False
    n_quar = sum(1 for c in surface.cases.values() if c["status"] == "quarantined")
    assert n_quar == 1
    assert not surface.active_cases()  # never auto-activates


# ---------------------------------------------------------------------------
# Scenario 3: ingest against an unknown baseline_version errors with guidance
# rather than filing a misleading case.
# ---------------------------------------------------------------------------


def test_ingest_against_unknown_baseline_errors_without_filing_a_case(tmp_path):
    slot = _developer_slot(baseline_version="base-1")
    _, slot_id = _portal_with_slot(tmp_path, slot)
    portal = load_portal(tmp_path, "sess1", "m.py", "m")

    bundle = bundle_from_dispute(
        spec_equivalence_key="eq-1", mode="adaptive", baseline_version="base-999",
        property_text="disputed output", input_sample={"xs": [1]},
    )
    with pytest.raises(IngestError):
        ingest_bundle(portal, bundle)

    surface = ContractSurface.from_slot(portal.slots[slot_id])
    assert not surface.cases


def test_ingest_unknown_slot_errors_without_filing_a_case(tmp_path):
    """A bundle with no baseline stamp is always accepted on baseline grounds,
    but must still fail if no slot's spec_equivalence_key matches at all."""
    slot = _developer_slot(spec_equivalence_key="eq-1")
    _portal_with_slot(tmp_path, slot)
    portal = load_portal(tmp_path, "sess1", "m.py", "m")

    bundle = bundle_from_dispute(
        spec_equivalence_key="eq-does-not-exist", mode="adaptive", baseline_version=None,
        property_text="disputed output", input_sample={},
    )
    with pytest.raises(IngestError):
        ingest_bundle(portal, bundle)


# ---------------------------------------------------------------------------
# Scenario 4: ingest is idempotent on the same bundle.
# ---------------------------------------------------------------------------


def test_ingest_is_idempotent_on_the_same_bundle(tmp_path):
    slot = _developer_slot(baseline_version="base-1")
    _, slot_id = _portal_with_slot(tmp_path, slot)

    bundle = bundle_from_dispute(
        spec_equivalence_key="eq-1", mode="adaptive", baseline_version="base-1",
        property_text="disputed output", input_sample={"xs": [1, 2, 3]},
    )

    portal = load_portal(tmp_path, "sess1", "m.py", "m")
    r1 = ingest_bundle(portal, bundle)
    save_portal(tmp_path, portal)

    portal2 = load_portal(tmp_path, "sess1", "m.py", "m")
    r2 = ingest_bundle(portal2, bundle)
    save_portal(tmp_path, portal2)

    assert r1.case_id == r2.case_id
    assert r1.created is True
    assert r2.created is False

    reloaded_slot = load_portal(tmp_path, "sess1", "m.py", "m").slots[slot_id]
    surface = ContractSurface.from_slot(reloaded_slot)
    assert len(surface.cases) == 1


# ---------------------------------------------------------------------------
# Scenario 5: adjudicating the case (existing dispute surface, U5) activates
# it -- signals a targeted regeneration, routing the slot through the melt
# path on next run. No new "activate" command; `semipy ingest` never does this.
# ---------------------------------------------------------------------------


def test_adjudicating_the_filed_case_via_existing_dispute_flow_signals_regen(tmp_path):
    slot = _developer_slot(baseline_version="base-1")
    portal_path, slot_id = _portal_with_slot(tmp_path, slot)
    portal = load_portal(tmp_path, "sess1", "m.py", "m")

    property_text = "a fully-null site must not change other sites' averages"
    bundle = bundle_from_dispute(
        spec_equivalence_key="eq-1", mode="adaptive", baseline_version="base-1",
        property_text=property_text, input_sample={"xs": [1, 2, 3]},
    )
    ingest_bundle(portal, bundle)
    save_portal(tmp_path, portal)

    # The quarantined report itself never activates -- only the developer's own
    # adjudication (here, `semipy dispute`, exactly as it already works) does.
    cmd_dispute(portal_path, slot_id, property_text, as_json=False)

    reloaded_slot = load_portal(tmp_path, "sess1", "m.py", "m").slots[slot_id]
    asserted = reloaded_slot.contract.get("asserted_properties", [])
    assert any(a.get("property") == property_text for a in asserted)
    decisions = reloaded_slot.decision_set["decisions"]
    assert len(decisions) == 1 and decisions[0]["status"] == "open"
    assert decisions[0]["resolution"]["regen_needed"] is True
