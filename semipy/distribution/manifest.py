"""Package-data manifest (U6, R11/R12, KTD-7): the index ``semipy build``
writes to ``_semiformal/manifest.json``, mapping each shipped slot to its
artifact module, floor-filtered contract, and distribution mode.

Keyed by ``spec_equivalence_key`` (``semipy.types.compute_spec_equivalence_key``),
not ``slot_id``: ``slot_id`` hashes in the absolute source file path
(``lowering._make_slot_id``), which differs between a developer's checkout and
an installed package's site-packages path. The equivalence key excludes path
and line number, so a consumer's installed copy resolves the same call site
regardless of where it was installed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.contract.serialize import dumps_pretty

# Bump when the serialized shape changes incompatibly. A loader rejects any
# version it does not recognize (mirrors semipy.contract.surface's convention).
SCHEMA_VERSION = 1


class ManifestSchemaError(ValueError):
    """A serialized manifest carries an unknown/unsupported schema version."""


@dataclass
class ManifestEntry:
    """One shipped slot: where its artifact/contract live, and its
    distribution mode.

    ``mode`` is a placeholder for U7 (slot distribution modes as a first-class
    authored concept), which has not landed yet -- ``build.py`` always emits
    ``"adaptive"`` today. A ``"frozen"`` entry skips the key-check branch
    entirely at resolution time (KTD-7): no key ever authorizes an adaptation
    attempt for a frozen slot, only the verify gate.

    ``classification`` records the behavioral-semver delta (KTD-8) versus the
    previous baseline's entry for this slot, via ``contract.surface.diff`` --
    U6 only records it; U12 owns enforcing it against a declared release type.
    """

    spec_equivalence_key: str
    slot_id: str
    artifact_module: str            # filename under _semiformal/artifacts/
    artifact_function: str          # function name inside that module
    contract_path: str              # filename under _semiformal/contracts/
    mode: str = "adaptive"          # "adaptive" | "frozen" (U7 placeholder)
    classification: str = "none"    # major | minor | patch | none (KTD-8, record-only)


@dataclass
class Manifest:
    """The full package-data index. ``entries`` is keyed by
    ``spec_equivalence_key``. ``baseline_hash`` is an order-independent content
    hash over every shipped slot's artifact + contract bytes -- it changes iff
    any shipped artifact or contract actually changed."""

    schema_version: int = SCHEMA_VERSION
    baseline_hash: str = ""
    entries: dict[str, ManifestEntry] = field(default_factory=dict)


def entry_to_dict(entry: ManifestEntry) -> dict[str, Any]:
    return {
        "spec_equivalence_key": entry.spec_equivalence_key,
        "slot_id": entry.slot_id,
        "artifact_module": entry.artifact_module,
        "artifact_function": entry.artifact_function,
        "contract_path": entry.contract_path,
        "mode": entry.mode,
        "classification": entry.classification,
    }


def entry_from_dict(d: dict[str, Any]) -> ManifestEntry:
    return ManifestEntry(
        spec_equivalence_key=str(d.get("spec_equivalence_key", "")),
        slot_id=str(d.get("slot_id", "")),
        artifact_module=str(d.get("artifact_module", "")),
        artifact_function=str(d.get("artifact_function", "")),
        contract_path=str(d.get("contract_path", "")),
        mode=str(d.get("mode", "adaptive") or "adaptive"),
        classification=str(d.get("classification", "none") or "none"),
    )


def manifest_to_dict(manifest: Manifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "baseline_hash": manifest.baseline_hash,
        "entries": {k: entry_to_dict(e) for k, e in manifest.entries.items()},
    }


def manifest_from_dict(d: dict[str, Any]) -> Manifest:
    if not isinstance(d, dict):
        raise ManifestSchemaError("manifest must be a JSON object")
    version = d.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ManifestSchemaError(
            f"unsupported manifest schema version {version!r} "
            f"(this semipy understands version {SCHEMA_VERSION})"
        )
    raw_entries = d.get("entries") or {}
    entries = {
        str(k): entry_from_dict(v) for k, v in raw_entries.items() if isinstance(v, dict)
    }
    return Manifest(
        schema_version=version,
        baseline_hash=str(d.get("baseline_hash", "") or ""),
        entries=entries,
    )


def manifest_to_json(manifest: Manifest) -> str:
    return dumps_pretty(manifest_to_dict(manifest))


def manifest_from_json(text: str) -> Manifest:
    return manifest_from_dict(json.loads(text))


def compute_baseline_hash(content_hashes: dict[str, str]) -> str:
    """Order-independent content hash over every shipped slot's artifact +
    contract bytes, keyed by ``spec_equivalence_key``. Adding, removing, or
    editing a shipped slot's content changes the hash; re-serializing the same
    content in a different key order does not."""
    parts = [f"{k}:{content_hashes[k]}" for k in sorted(content_hashes)]
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
