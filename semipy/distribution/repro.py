"""Repro bundles and ingestion (U10, R18): a structured, redacted export from a
consumer-site ``ScopeViolation``, deopt, or dispute, and the developer-side
``ingest`` that files it as a quarantined candidate case.

A consumer's installed-package ``slot_id`` differs from the developer's own
checkout ``slot_id`` (``slot_id`` hashes in the absolute source path -- see
``distribution/manifest.py``'s module docstring), so a bundle identifies its
slot by ``spec_equivalence_key`` instead: ``ingest`` finds "the same slot" by
scanning the developer's own portal for a slot whose
``slot.slot_spec["spec_equivalence_key"]`` matches.

Filing a bundle never activates it: the case is created ``status="quarantined"``,
``provenance="consumer-report"``. A human developer adjudicates it with the
existing ``dispute``/``assert-decision``/``pick-decision`` surfaces (U5/U9),
exactly as they work today -- this module has no "activate" step of its own.
"""
from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from semipy.contract.access import get_contract, save_contract
from semipy.contract.models import ContractCase
from semipy.contract.redaction import redact_value
from semipy.contract.serialize import dumps_pretty

# Bump when the serialized shape changes incompatibly. A loader rejects any
# version it does not recognize (mirrors distribution.manifest's convention).
SCHEMA_VERSION = 1


class ReproBundleSchemaError(ValueError):
    """A serialized repro bundle carries an unknown/unsupported schema version."""


class IngestError(Exception):
    """Guidance error: the bundle cannot be filed as a case (unknown slot or
    baseline). Raised instead of filing a misleading case."""


@dataclass
class ReproBundle:
    """One consumer-reported event, redacted and ready to ship across to the
    developer's side. ``event_kind`` is one of "scope_violation" / "deopt" /
    "dispute". A scope_violation/deopt carries the violated conjunct and the
    observed input profile; a dispute carries the disputed property and the
    (redacted) input the consumer wants to dispute the output for."""

    schema_version: int = SCHEMA_VERSION
    spec_equivalence_key: str = ""
    baseline_version: Optional[str] = None
    mode: str = ""
    event_kind: str = ""
    violated_conjunct: str = ""
    violated_var: str = ""
    observed_profile: dict[str, Any] = field(default_factory=dict)
    property_text: str = ""
    input_sample: dict[str, Any] = field(default_factory=dict)
    environment_digest: str = ""


def _environment_digest() -> str:
    return f"{platform.python_version()}/{platform.system()}"


def redact_bundle(bundle: ReproBundle) -> ReproBundle:
    """Scrub secret-shaped content from a bundle's ``observed_profile`` /
    ``input_sample`` in place before it is considered exportable (R18)."""
    profile, _ = redact_value(bundle.observed_profile)
    sample, _ = redact_value(bundle.input_sample)
    bundle.observed_profile = profile
    bundle.input_sample = sample
    return bundle


def bundle_from_scope_violation(
    exc: Any, *, spec_equivalence_key: str, mode: str, baseline_version: Optional[str]
) -> ReproBundle:
    """Build a bundle from a raised ``distribution.runtime.ScopeViolation``."""
    bundle = ReproBundle(
        spec_equivalence_key=spec_equivalence_key,
        baseline_version=baseline_version,
        mode=mode,
        event_kind="scope_violation",
        violated_conjunct=str(exc.bundle.get("violated_conjunct", "") or ""),
        violated_var=str(exc.bundle.get("violated_var", "") or ""),
        observed_profile=dict(exc.bundle.get("observed_profile") or {}),
        environment_digest=_environment_digest(),
    )
    return redact_bundle(bundle)


def bundle_from_deopt(
    *,
    spec_equivalence_key: str,
    mode: str,
    baseline_version: Optional[str],
    violated_conjunct: str,
    violated_var: str,
    observed_profile: dict[str, Any],
) -> ReproBundle:
    """Build a bundle from a deopt: a frozen-mode fallback that ran outside its
    shipped scope but passed the verify gate (so it never raised). Same shape
    as a scope_violation bundle, just constructed from the violated-conjunct
    data directly rather than from an exception object."""
    bundle = ReproBundle(
        spec_equivalence_key=spec_equivalence_key,
        baseline_version=baseline_version,
        mode=mode,
        event_kind="deopt",
        violated_conjunct=violated_conjunct,
        violated_var=violated_var,
        observed_profile=dict(observed_profile),
        environment_digest=_environment_digest(),
    )
    return redact_bundle(bundle)


def bundle_from_dispute(
    *,
    spec_equivalence_key: str,
    mode: str,
    baseline_version: Optional[str],
    property_text: str,
    input_sample: dict[str, Any],
) -> ReproBundle:
    """Build a bundle from a consumer disputing the output for a given input."""
    bundle = ReproBundle(
        spec_equivalence_key=spec_equivalence_key,
        baseline_version=baseline_version,
        mode=mode,
        event_kind="dispute",
        property_text=property_text,
        input_sample=dict(input_sample),
        environment_digest=_environment_digest(),
    )
    return redact_bundle(bundle)


# ---------------------------------------------------------------------------
# (De)serialization -- versioned JSON, mirrors distribution.manifest.
# ---------------------------------------------------------------------------


def bundle_to_dict(bundle: ReproBundle) -> dict[str, Any]:
    return {
        "schema_version": bundle.schema_version,
        "spec_equivalence_key": bundle.spec_equivalence_key,
        "baseline_version": bundle.baseline_version,
        "mode": bundle.mode,
        "event_kind": bundle.event_kind,
        "violated_conjunct": bundle.violated_conjunct,
        "violated_var": bundle.violated_var,
        "observed_profile": dict(bundle.observed_profile),
        "property_text": bundle.property_text,
        "input_sample": dict(bundle.input_sample),
        "environment_digest": bundle.environment_digest,
    }


def bundle_from_dict(d: dict[str, Any]) -> ReproBundle:
    if not isinstance(d, dict):
        raise ReproBundleSchemaError("repro bundle must be a JSON object")
    version = d.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ReproBundleSchemaError(
            f"unsupported repro bundle schema version {version!r} "
            f"(this semipy understands version {SCHEMA_VERSION})"
        )
    return ReproBundle(
        schema_version=version,
        spec_equivalence_key=str(d.get("spec_equivalence_key", "") or ""),
        baseline_version=d.get("baseline_version"),
        mode=str(d.get("mode", "") or ""),
        event_kind=str(d.get("event_kind", "") or ""),
        violated_conjunct=str(d.get("violated_conjunct", "") or ""),
        violated_var=str(d.get("violated_var", "") or ""),
        observed_profile=dict(d.get("observed_profile") or {}),
        property_text=str(d.get("property_text", "") or ""),
        input_sample=dict(d.get("input_sample") or {}),
        environment_digest=str(d.get("environment_digest", "") or ""),
    )


def bundle_to_json(bundle: ReproBundle) -> str:
    return dumps_pretty(bundle_to_dict(bundle))


def bundle_from_json(text: str) -> ReproBundle:
    return bundle_from_dict(json.loads(text))


def write_bundle(bundle: ReproBundle, path: Path | str) -> None:
    Path(path).write_text(bundle_to_json(bundle), encoding="utf-8")


def read_bundle(path: Path | str) -> ReproBundle:
    return bundle_from_json(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Developer-side ingest.
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    case_id: str
    slot_id: str
    created: bool  # False when this exact bundle was already filed (idempotent re-ingest)


def _find_slot(portal: Any, spec_equivalence_key: str) -> Any:
    """Scan the portal for the slot whose persisted meaning matches the
    bundle's ``spec_equivalence_key`` -- slot_id itself is not stable across
    a consumer's install location vs. the developer's checkout."""
    for slot in portal.slots.values():
        spec = getattr(slot, "slot_spec", None) or {}
        if isinstance(spec, dict) and spec.get("spec_equivalence_key") == spec_equivalence_key:
            return slot
    return None


def _baseline_known(slot: Any, baseline_version: Optional[str]) -> bool:
    """A bundle with no baseline stamp is always accepted (pre-U8/U9 consumer,
    or one with no package data installed at all -- still a legitimate
    report). A stamped baseline is "known" iff some commit on this slot was
    itself stamped against that same baseline version."""
    if baseline_version is None:
        return True
    for commit in slot.commits.values():
        record = getattr(commit, "commitment_record", None) or {}
        if record.get("baseline_version") == baseline_version:
            return True
    return False


def compute_bundle_case_id(bundle: ReproBundle) -> str:
    """Content-addressed case id from the bundle's own content, so re-ingesting
    the exact same bundle produces the exact same case_id (idempotent via
    ``SlotContract.add``'s existing overwrite-by-id behavior)."""
    data = bundle.input_sample if bundle.event_kind == "dispute" else bundle.observed_profile
    raw = (
        f"{bundle.spec_equivalence_key}\0{bundle.event_kind}\0{bundle.violated_conjunct}\0"
        f"{bundle.property_text}\0{sorted((data or {}).items())}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _reason(bundle: ReproBundle) -> str:
    detail = bundle.violated_conjunct or bundle.property_text
    return f"consumer report ({bundle.event_kind}): {detail}"


def ingest_bundle(portal: Any, bundle: ReproBundle) -> IngestResult:
    """File *bundle* as a quarantined candidate case on the matching slot in
    *portal* (R18). Raises ``IngestError`` (guidance, not a filed case) when
    no slot matches or the bundle's baseline is unknown. Never activates the
    case -- adjudication via the existing dispute/assert-decision/pick-decision
    surfaces is what does that; caller is responsible for persisting the portal.
    """
    slot = _find_slot(portal, bundle.spec_equivalence_key)
    if slot is None:
        raise IngestError(
            f"no slot in this portal matches spec_equivalence_key "
            f"{bundle.spec_equivalence_key!r}; the bundle may be from a different "
            "package or checkout"
        )
    if not _baseline_known(slot, bundle.baseline_version):
        raise IngestError(
            f"unknown baseline_version {bundle.baseline_version!r} for this slot; "
            "the bundle may be stale or from a different package version"
        )

    is_dispute = bundle.event_kind == "dispute"
    case_id = compute_bundle_case_id(bundle)
    case = ContractCase(
        case_id=case_id,
        kind="example",
        input_sample=dict(bundle.input_sample) if is_dispute else {},
        source_profile={} if is_dispute else dict(bundle.observed_profile),
        reason=_reason(bundle),
        provenance="consumer-report",
        status="quarantined",
        ship=False,
    )
    contract = get_contract(slot)
    created = case_id not in contract.cases
    contract.add(case)
    save_contract(slot, contract)
    return IngestResult(case_id=case_id, slot_id=getattr(slot, "slot_id", "") or "", created=created)
