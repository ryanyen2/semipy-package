"""U1: the contract surface -- assembly, versioned JSON round-trip, and the
behavioral-semver diff (R1, R2)."""
from __future__ import annotations

import json

import pytest

from semipy.cli import cmd_contract_diff, cmd_contract_show
from semipy.contract.access import save_contract
from semipy.contract.models import ContractCase, SlotContract
from semipy.contract.surface import (
    SCHEMA_VERSION,
    ContractSchemaError,
    ContractSurface,
    diff,
    surface_from_dict,
    surface_from_json,
    surface_to_dict,
    surface_to_json,
)
from semipy.history.version_control import Portal, Slot
from semipy.kernel.operators import FreezeCertificate, FreezeEvent, append_freeze_event
from semipy.kernel.tree import Guard, Hardness, Node, NodeKind, save_tree
from semipy.store import save_portal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _slot(slot_id: str = "s1", spec_text: str = "normalize the row", expected_type: str = "<class 'dict'>") -> Slot:
    return Slot(
        slot_id=slot_id,
        call_site_info={},
        function_name_base="f",
        slot_spec={"spec_text": spec_text, "expected_type": expected_type},
    )


def _add_cert(slot: Slot, *, licensed: bool) -> None:
    cert = FreezeCertificate(
        epsilon=0.05, delta=0.1, gamma=1.0, budget_total=10, budget_spent=3,
        held_out_pass_fraction=1.0, mdl_gain=5.0, licensed=licensed,
        refusal_reasons=[] if licensed else ["output has no usable equivalence (free text)"],
    )
    append_freeze_event(slot, FreezeEvent(certificate=cert, node_id="root", source_len=100, timestamp=1.0))


def _surface_from_cases(status_map, *, guards=None, scope_ref=None, certified=None) -> ContractSurface:
    """Build a slot with the given cases (id -> status) and assemble its surface."""
    slot = _slot()
    contract = SlotContract()
    for cid, status in status_map.items():
        contract.cases[cid] = ContractCase(case_id=cid, kind="invariant", invariant="non_empty", status=status)
    save_contract(slot, contract)
    if guards:
        root = Node(
            node_id="root", kind=NodeKind.BRANCH, hardness=Hardness.PLASTIC,
            guards=[Guard(predicate_source=g) for g in guards],
        )
        save_tree(slot, root)
    if certified is not None:
        _add_cert(slot, licensed=certified)
    return ContractSurface.from_slot(slot, scope_predicate_ref=scope_ref)


# ---------------------------------------------------------------------------
# Scenario 1: round-trip identity
# ---------------------------------------------------------------------------


def test_surface_round_trips_through_json_identically():
    slot = _slot()
    contract = SlotContract()
    contract.add(ContractCase(case_id="c1", kind="invariant", invariant="non_empty"))
    contract.add(ContractCase(case_id="c2", kind="metamorphic", relation="whitespace_invariance"))
    save_contract(slot, contract)
    save_tree(
        slot,
        Node(
            node_id="root", kind=NodeKind.BRANCH, hardness=Hardness.PLASTIC,
            guards=[Guard(predicate_source="isinstance(x, int)")],
            children=[Node(node_id="root.0", kind=NodeKind.OPAQUE, hardness=Hardness.PLASTIC, artifact="pass")],
        ),
    )
    _add_cert(slot, licensed=True)

    surface = ContractSurface.from_slot(slot)
    assert surface_from_json(surface_to_json(surface)) == surface
    assert surface_from_dict(surface_to_dict(surface)) == surface


# ---------------------------------------------------------------------------
# Scenario 2: no certificate -> explicit uncertified marker (D4)
# ---------------------------------------------------------------------------


def test_slot_without_certificate_is_uncertified_partial_contract():
    slot = _slot(spec_text="lay out these nodes and edges as a readable SVG diagram")
    contract = SlotContract()
    # A checkable sub-property (D4: no overlapping boxes / well-formed SVG live here).
    contract.add(ContractCase(case_id="inv1", kind="invariant", invariant="non_empty"))
    save_contract(slot, contract)

    surface = ContractSurface.from_slot(slot)
    assert surface.certified is False
    assert surface.uncertified is True
    assert surface.certificate is None
    # It still ships as a partial object: the sub-property is present and checkable.
    assert [c["case_id"] for c in surface.active_cases()] == ["inv1"]

    d = surface_to_dict(surface)
    assert d["certified"] is False
    assert d["certificate"] is None


def test_certified_slot_carries_the_licensing_certificate():
    surface = _surface_from_cases({"c1": "active"}, certified=True)
    assert surface.certified is True
    assert surface.uncertified is False
    assert surface.certificate is not None
    assert surface.certificate["licensed"] is True


# ---------------------------------------------------------------------------
# Scenario 3-6: behavioral-semver diff (R2)
# ---------------------------------------------------------------------------


def test_diff_case_added_classifies_patch():
    base = _surface_from_cases({"c1": "active"})
    newer = _surface_from_cases({"c1": "active", "c2": "active"})
    result = diff(base, newer)
    assert result.classification == "patch"
    assert result.added_cases == ["c2"]
    assert result.superseded_cases == []


def test_diff_case_superseded_classifies_major():
    base = _surface_from_cases({"c1": "active"})
    # A supersede leaves the old case present-but-inactive and adds its replacement.
    newer = _surface_from_cases({"c1": "superseded", "c2": "active"})
    result = diff(base, newer)
    assert result.classification == "major"
    assert result.superseded_cases == ["c1"]


def test_diff_regime_added_classifies_minor():
    base = _surface_from_cases({"c1": "active"})
    newer = _surface_from_cases({"c1": "active"}, guards=["msg.kind == 'conflict'"])
    result = diff(base, newer)
    assert result.classification == "minor"
    assert result.added_regimes == ["msg.kind == 'conflict'"]


def test_diff_certificate_invalidated_classifies_major():
    base = _surface_from_cases({"c1": "active"}, certified=True)
    newer = _surface_from_cases({"c1": "active"}, certified=False)
    result = diff(base, newer)
    assert result.classification == "major"
    assert result.certificate_invalidated is True


def test_unknown_schema_version_fails_with_clear_error():
    d = surface_to_dict(_surface_from_cases({"c1": "active"}))
    d["schema_version"] = 999
    with pytest.raises(ContractSchemaError) as exc:
        surface_from_dict(d)
    msg = str(exc.value)
    assert "999" in msg
    assert "schema version" in msg


# ---------------------------------------------------------------------------
# Edge cases: empty slot, self-diff, precedence
# ---------------------------------------------------------------------------


def test_empty_slot_yields_an_empty_uncertified_surface():
    surface = ContractSurface.from_slot(_slot())
    assert surface.cases == {}
    assert surface.regimes == []
    assert surface.relations == []
    assert surface.certified is False
    assert surface.scope_predicate_ref is None
    assert surface_from_json(surface_to_json(surface)) == surface


def test_diff_of_a_surface_against_itself_is_a_no_op():
    surface = _surface_from_cases({"c1": "active"}, guards=["isinstance(x, int)"], certified=True)
    result = diff(surface, surface)
    assert result.classification == "none"
    assert result.reasons == []


def test_diff_reports_highest_severity_but_lists_every_change():
    base = _surface_from_cases({"c1": "active"})
    # A release that both supersedes a pinned case (major) and adds a regime (minor)
    # and adds evidence (patch): classification is the max, reasons name them all.
    newer = _surface_from_cases({"c1": "superseded", "c2": "active"}, guards=["isinstance(x, int)"])
    result = diff(base, newer)
    assert result.classification == "major"
    assert result.superseded_cases == ["c1"]
    assert result.added_regimes == ["isinstance(x, int)"]
    assert "c2" in result.added_cases
    assert len(result.reasons) >= 3


# ---------------------------------------------------------------------------
# Scope-predicate-ref seam (for U2) and ship/provenance fields (for U3)
# ---------------------------------------------------------------------------


def test_scope_predicate_ref_seam_round_trips_and_diffs_as_minor():
    base = _surface_from_cases({"c1": "active"})
    scoped = _surface_from_cases({"c1": "active"}, scope_ref="cols == {'a', 'b'}")
    assert scoped.scope_predicate_ref == "cols == {'a', 'b'}"
    assert surface_from_json(surface_to_json(scoped)) == scoped
    result = diff(base, scoped)
    assert result.classification == "minor"
    assert result.scope_changed is True


def test_from_slot_reads_scope_predicate_ref_left_on_the_slot():
    # Demonstrates the seam: whatever U2 stores as ``slot.scope_predicate_ref``
    # flows into the surface with no change here.
    slot = _slot()
    slot.scope_predicate_ref = "len(x) > 0"  # type: ignore[attr-defined]
    surface = ContractSurface.from_slot(slot)
    assert surface.scope_predicate_ref == "len(x) > 0"


def test_ship_flag_and_provenance_serialize_on_cases():
    slot = _slot()
    contract = SlotContract()
    contract.cases["c1"] = ContractCase(
        case_id="c1", kind="invariant", invariant="non_empty",
        ship=True, provenance="synthetic", source_locator="", snapshot_fingerprint="",
    )
    save_contract(slot, contract)
    surface = ContractSurface.from_slot(slot)
    assert surface.cases["c1"]["ship"] is True
    assert surface.cases["c1"]["provenance"] == "synthetic"
    assert surface_from_json(surface_to_json(surface)) == surface


def test_only_active_metamorphic_relations_are_summarized():
    slot = _slot()
    contract = SlotContract()
    contract.cases["m1"] = ContractCase(case_id="m1", kind="metamorphic", relation="whitespace_invariance")
    contract.cases["m2"] = ContractCase(
        case_id="m2", kind="metamorphic", relation="dict_key_order_invariance", status="superseded"
    )
    save_contract(slot, contract)
    surface = ContractSurface.from_slot(slot)
    assert surface.relations == ["whitespace_invariance"]


# ---------------------------------------------------------------------------
# CLI integration: `semipy contract show` / `contract diff`
# ---------------------------------------------------------------------------


def _write_portal(tmp_path, slot: Slot):
    cache_dir = tmp_path / ".semiformal"
    cache_dir.mkdir()
    portal = Portal(session_id="sess", source_file="f.py", module_name="mod")
    portal.slots[slot.slot_id] = slot
    save_portal(cache_dir, portal)
    return cache_dir / "sess.portal.json"


def test_cli_contract_show_json_renders_the_partial_boundary(tmp_path, capsys):
    slot = _slot(spec_text="lay out these nodes as an SVG diagram")
    contract = SlotContract()
    contract.add(ContractCase(case_id="inv1", kind="invariant", invariant="non_empty"))
    save_contract(slot, contract)
    portal_path = _write_portal(tmp_path, slot)

    cmd_contract_show(portal_path, "s1", True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["certified"] is False
    assert payload["certificate"] is None
    assert "inv1" in payload["cases"]


def test_cli_contract_show_text_shows_uncertified(tmp_path, capsys):
    slot = _slot(spec_text="lay out these nodes as an SVG diagram")
    save_contract(slot, SlotContract())
    portal_path = _write_portal(tmp_path, slot)

    cmd_contract_show(portal_path, "s1", False)
    out = capsys.readouterr().out
    assert "UNCERTIFIED" in out
    assert "lay out these nodes" in out


def test_cli_contract_diff_classifies_from_two_surface_files(tmp_path, capsys):
    old = _surface_from_cases({"c1": "active"})
    new = _surface_from_cases({"c1": "active", "c2": "active"})
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(surface_to_json(old), encoding="utf-8")
    new_path.write_text(surface_to_json(new), encoding="utf-8")

    cmd_contract_diff(old_path, new_path, True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["classification"] == "patch"
    assert payload["added_cases"] == ["c2"]


def test_cli_contract_diff_rejects_unknown_schema_version(tmp_path):
    surface = _surface_from_cases({"c1": "active"})
    d = surface_to_dict(surface)
    d["schema_version"] = 999
    bad = tmp_path / "bad.json"
    good = tmp_path / "good.json"
    bad.write_text(json.dumps(d), encoding="utf-8")
    good.write_text(surface_to_json(surface), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cmd_contract_diff(bad, good, False)
    assert "999" in str(exc.value)
