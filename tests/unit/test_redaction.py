"""U3: capture-time redaction and provenance-based ship defaults (R6, R14)."""
from __future__ import annotations

from pathlib import Path

from semipy.contract.maintainer import _apply_capture_time_redaction
from semipy.contract.models import ContractCase, SlotContract
from semipy.contract.redaction import (
    EXTERNAL_PROVENANCE,
    apply_capture_time_policy,
    default_ship_flag,
    redact_case,
    redact_value,
)
from semipy.documents import capture_external_provenance, load_document_text_with_provenance


# ---------------------------------------------------------------------------
# Scenario 1: a webhook payload with an Authorization header persists with the
# token masked (D2).
# ---------------------------------------------------------------------------


def test_webhook_authorization_header_is_masked():
    case = ContractCase(
        case_id="c1",
        kind="invariant",
        invariant="non_empty",
        input_sample={
            "payload": {
                "headers": {"Authorization": "Bearer sk_live_abcdef1234567890"},
                "body": {"event": "order.created"},
            }
        },
    )
    result = redact_case(case)
    assert result.changed
    headers = case.input_sample["payload"]["headers"]
    assert headers["Authorization"] == "<REDACTED:authorization>"
    assert "sk_live_abcdef1234567890" not in str(case.input_sample)
    # The rest of the payload (the actual behavior under test) survives intact.
    assert case.input_sample["payload"]["body"] == {"event": "order.created"}
    # Not a lost target -- the payload dict as a whole still pins real structure.
    assert not result.target_lost


def test_bearer_token_embedded_in_free_text_is_masked():
    value, changed = redact_value("call with header Authorization: Bearer abc.def-123456")
    assert changed
    assert "abc.def-123456" not in value
    assert "<REDACTED:bearer_token>" in value


def test_email_is_masked():
    value, changed = redact_value({"contact": "alice@example.com"})
    assert changed
    assert value["contact"] == "<REDACTED:email>"


def test_long_digit_run_is_masked():
    value, changed = redact_value("card 4111111111111111 on file")
    assert changed
    assert "4111111111111111" not in value
    assert "<REDACTED:digits>" in value


def test_api_key_shaped_token_is_masked():
    value, changed = redact_value("key=aGVsbG8gd29ybGQgc2VjcmV0MTIz done")
    assert changed
    assert "aGVsbG8gd29ybGQgc2VjcmV0MTIz" not in value
    assert "<REDACTED:api_key>" in value


def test_non_secret_content_is_left_alone():
    value, changed = redact_value({"name": "Acme Widget", "qty": 3})
    assert not changed
    assert value == {"name": "Acme Widget", "qty": 3}


# ---------------------------------------------------------------------------
# Scenario 2: a case built from a file input records the locator and snapshot
# fingerprint (generic stand-in for the PDF case study).
# ---------------------------------------------------------------------------


def test_file_input_records_locator_and_snapshot_fingerprint(tmp_path: Path):
    doc = tmp_path / "agreement.txt"
    doc.write_text("This agreement is between Acme and Bob.")

    text, provenance = load_document_text_with_provenance(doc)
    assert text == "This agreement is between Acme and Bob."
    assert provenance.locator == str(doc.resolve())
    assert provenance.snapshot_fingerprint
    assert provenance.profile["kind"] == "file"
    assert provenance.profile["size_chars"] == len(text)

    # Re-capturing the same content yields the same fingerprint (stable identity).
    same = capture_external_provenance(doc, text)
    assert same.snapshot_fingerprint == provenance.snapshot_fingerprint

    # A case can carry this provenance on its dedicated fields (R6).
    case = ContractCase(
        case_id="c2",
        kind="invariant",
        invariant="non_empty",
        input_sample={"doc": text},
        provenance=EXTERNAL_PROVENANCE,
        source_locator=provenance.locator,
        snapshot_fingerprint=provenance.snapshot_fingerprint,
        source_profile=provenance.profile,
    )
    assert case.source_locator == str(doc.resolve())
    assert case.snapshot_fingerprint == provenance.snapshot_fingerprint


def test_different_content_yields_different_snapshot_fingerprint(tmp_path: Path):
    a = capture_external_provenance(tmp_path / "a.txt", "version one")
    b = capture_external_provenance(tmp_path / "a.txt", "version two")
    assert a.snapshot_fingerprint != b.snapshot_fingerprint


# ---------------------------------------------------------------------------
# Scenario 3: ship-flag defaults follow the provenance rule (R14).
# ---------------------------------------------------------------------------


def test_ship_flag_defaults_follow_provenance_rule():
    assert default_ship_flag("external") is False
    assert default_ship_flag("synthetic") is True
    assert default_ship_flag("relation") is True
    assert default_ship_flag("user") is True
    assert default_ship_flag("") is True  # unset/no-category defaults like synthetic


def test_apply_capture_time_policy_sets_ship_from_provenance():
    contract = SlotContract()
    external_case = ContractCase(
        case_id="c3", kind="invariant", invariant="non_empty",
        input_sample={"page": "hello world"}, provenance="external",
    )
    synthetic_case = ContractCase(
        case_id="c4", kind="invariant", invariant="non_empty",
        input_sample={"page": "hello world"}, provenance="synthetic",
    )
    contract.add(external_case)
    contract.add(synthetic_case)

    apply_capture_time_policy(external_case, contract)
    apply_capture_time_policy(synthetic_case, contract)

    assert external_case.ship is False
    assert synthetic_case.ship is True


# ---------------------------------------------------------------------------
# Scenario 4: a redacted case that loses its assertable target is
# auto-quarantined rather than silently weakened.
# ---------------------------------------------------------------------------


def test_case_whose_primary_input_is_entirely_secret_is_auto_quarantined():
    contract = SlotContract()
    case = ContractCase(
        case_id="c5", kind="invariant", invariant="type_match", expected_type="str",
        input_sample={"api_key": "sk_live_abcdef1234567890"},
    )
    contract.add(case)

    result = apply_capture_time_policy(case, contract)

    assert result.target_lost
    assert case.status == "quarantined"
    assert case.input_sample["api_key"] == "<REDACTED:api_key>"


def test_example_case_whose_pinned_output_is_entirely_secret_is_auto_quarantined():
    contract = SlotContract()
    case = ContractCase(
        case_id="c6", kind="example",
        input_sample={"text": "echo the token"},
        expected_repr="'Bearer abcdefghijklmnop123456'",
        expected_type="str",
    )
    contract.add(case)

    result = apply_capture_time_policy(case, contract)

    assert result.target_lost
    assert case.status == "quarantined"


def test_partial_redaction_within_a_larger_payload_does_not_quarantine():
    contract = SlotContract()
    case = ContractCase(
        case_id="c7", kind="invariant", invariant="non_empty",
        input_sample={"payload": {"Authorization": "Bearer xyz", "event": "order.created"}},
    )
    contract.add(case)

    result = apply_capture_time_policy(case, contract)

    assert result.changed
    assert not result.target_lost
    assert case.status == "active"


# ---------------------------------------------------------------------------
# Scenario 5: re-running the same capture is idempotent -- no double-masking.
# ---------------------------------------------------------------------------


def test_redaction_is_idempotent():
    case = ContractCase(
        case_id="c8", kind="invariant", invariant="non_empty",
        input_sample={
            "payload": {
                "Authorization": "Bearer sk_live_abcdef1234567890",
                "contact": "alice@example.com",
                "card": "4111111111111111",
            }
        },
    )
    first = redact_case(case)
    assert first.changed
    snapshot = dict(case.input_sample)

    second = redact_case(case)
    assert not second.changed
    assert case.input_sample == snapshot


def test_apply_capture_time_policy_is_idempotent_on_ship_and_quarantine():
    contract = SlotContract()
    case = ContractCase(
        case_id="c9", kind="invariant", invariant="non_empty",
        input_sample={"payload": {"Authorization": "Bearer abc123456789"}},
        provenance="external",
    )
    contract.add(case)

    apply_capture_time_policy(case, contract)
    ship_after_first = case.ship
    status_after_first = case.status

    apply_capture_time_policy(case, contract)
    assert case.ship == ship_after_first
    assert case.status == status_after_first


# ---------------------------------------------------------------------------
# maintainer.py wiring: newly-persisted cases are redacted and ship-defaulted;
# a case already in the contract before this call is left untouched.
# ---------------------------------------------------------------------------


def test_maintainer_redaction_hook_only_touches_new_cases():
    contract = SlotContract()
    already_persisted = ContractCase(
        case_id="old", kind="invariant", invariant="non_empty",
        input_sample={"secret": "sk_live_abcdef1234567890"}, ship=True,
    )
    contract.cases["old"] = already_persisted  # simulate a prior save (bypasses .add())

    fresh = ContractCase(
        case_id="new", kind="invariant", invariant="non_empty",
        input_sample={"secret": "sk_live_abcdef1234567890"},
    )
    contract.add(fresh)

    n_quarantined = _apply_capture_time_redaction(contract, {"new"})

    # Untouched: not in the new-case set, so its already-decided ship flag and
    # unredacted content are left exactly as persisted.
    assert already_persisted.input_sample["secret"] == "sk_live_abcdef1234567890"
    assert already_persisted.ship is True

    # New case: redacted, ship-defaulted, and auto-quarantined (its whole
    # primary input was the secret).
    assert fresh.input_sample["secret"] == "<REDACTED:secret>"
    assert fresh.status == "quarantined"
    assert n_quarantined == 1
