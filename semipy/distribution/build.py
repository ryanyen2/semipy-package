"""``semipy build``: distill a portal into consumer-facing package data (U6,
R11/R12, KTD-7/KTD-8).

Writes ``_semiformal/`` next to a library's modules:
  - ``manifest.json`` -- schema version, baseline content hash, and per-slot
    mode + artifact/contract refs + semver classification vs the previous
    baseline.
  - ``artifacts/<key>.py`` -- one module per shipped slot, holding the active
    commit's generated source as-is (regime-guard dispatch, if any, is already
    baked into that source: ``kernel.tree``'s guard tree is a *descriptive*
    decomposition of it, not a separate execution path).
  - ``contracts/<key>.json`` -- the slot's floor-filtered contract (R14): only
    ``ship=True`` active cases (and the relations they imply), plus the
    regimes/certificate (structural, shipped as-is) and the slot's minted
    scope predicate.

Only slots whose ``slot_spec`` carries a ``spec_equivalence_key`` are shipped
-- that key (not ``slot_id``, which bakes in the absolute source path) is what
lets an installed copy resolve the same call site regardless of install
location; legacy slots without one are skipped with a warning.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from semipy.contract.serialize import dumps_pretty
from semipy.contract.surface import ContractSurface, diff, surface_from_dict, surface_to_dict
from semipy.distribution.manifest import (
    Manifest,
    ManifestEntry,
    compute_baseline_hash,
    manifest_from_json,
    manifest_to_json,
)
from semipy.history.version_control import Portal, Slot
from semipy.store import _dispatch_source_only, _get_active_commit, _source_with_function_name

ARTIFACT_FUNCTION_NAME = "run"
ARTIFACTS_DIR = "artifacts"
CONTRACTS_DIR = "contracts"
MANIFEST_FILENAME = "manifest.json"


@dataclass
class BuildWarning:
    slot_id: str
    message: str


@dataclass
class BuildResult:
    manifest: Manifest
    warnings: list[BuildWarning] = field(default_factory=list)


def _floor_filtered_surface(slot: Slot) -> ContractSurface:
    """R14: only ``ship=True`` active cases (and the relations they imply)
    survive into the shipped contract. Regimes/certificate ship as-is -- they
    are structural (the artifact's own control flow, the freeze decision),
    not per-case evidence."""
    surface = ContractSurface.from_slot(slot)
    shipped_cases = {
        cid: c
        for cid, c in surface.cases.items()
        if c.get("status") == "active" and c.get("ship")
    }
    surface.relations = sorted(
        {
            c.get("relation")
            for c in shipped_cases.values()
            if c.get("kind") == "metamorphic" and c.get("relation")
        }
    )
    surface.cases = shipped_cases
    return surface


def _scope_predicate_dict(slot: Slot, commit_id: str) -> Optional[dict[str, Any]]:
    """The real minted ``ScopePredicate`` for the active commit: it lives in
    ``slot.advisor_state`` (U2's reuse fast path), not in
    ``ContractSurface.scope_predicate_ref`` (unused in practice)."""
    adv = slot.advisor_state if isinstance(slot.advisor_state, dict) else {}
    return (adv.get("scope_predicates") or {}).get(commit_id)


def _package_contract_dict(
    surface: ContractSurface, scope_dict: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """The shipped contract JSON: the floor-filtered surface plus the raw
    scope predicate dict as a sibling field (the surface's own
    ``scope_predicate_ref`` is not where the real predicate lives)."""
    return {
        "schema_version": 1,
        "surface": surface_to_dict(surface),
        "scope_predicate": scope_dict,
    }


def build_package_data(
    portal: Portal,
    output_dir: Path,
    *,
    previous_package_dir: Optional[Path] = None,
    release_type: Optional[str] = None,
) -> BuildResult:
    """Distill *portal* into package data under *output_dir* (the
    ``_semiformal/`` directory itself; the caller picks where it lives --
    conventionally next to the library's top-level package).

    ``previous_package_dir``, when given, points at a prior build's
    ``_semiformal/`` directory: each shipped slot present in both builds is
    classified against its previous contract by ``contract.surface.diff``
    (KTD-8). A slot with no previous entry has nothing to diff against, so its
    classification stays ``"none"`` -- U6 does not invent a value beyond what
    ``diff`` already classifies. With ``release_type`` declared, a classified
    ``major``/``minor`` delta under a ``patch`` (or ``major`` under `minor``)
    release is recorded as a build warning; U12 owns enforcing it as a gate.
    """
    artifacts_dir = output_dir / ARTIFACTS_DIR
    contracts_dir = output_dir / CONTRACTS_DIR
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    contracts_dir.mkdir(parents=True, exist_ok=True)

    previous_manifest: Optional[Manifest] = None
    if previous_package_dir is not None:
        prev_manifest_path = previous_package_dir / MANIFEST_FILENAME
        if prev_manifest_path.exists():
            previous_manifest = manifest_from_json(prev_manifest_path.read_text(encoding="utf-8"))

    entries: dict[str, ManifestEntry] = {}
    content_hashes: dict[str, str] = {}
    warnings: list[BuildWarning] = []

    for slot in portal.slots.values():
        active = _get_active_commit(slot)
        if active is None:
            continue
        slot_spec = slot.slot_spec if isinstance(slot.slot_spec, dict) else {}
        key = slot_spec.get("spec_equivalence_key")
        if not key:
            warnings.append(
                BuildWarning(slot.slot_id, "no spec_equivalence_key on slot_spec; skipped (legacy slot)")
            )
            continue

        surface = _floor_filtered_surface(slot)
        scope_dict = _scope_predicate_dict(slot, active.commit_id)
        contract_text = dumps_pretty(_package_contract_dict(surface, scope_dict))

        source_only = _dispatch_source_only(active.generated_source)
        artifact_source = _source_with_function_name(source_only, ARTIFACT_FUNCTION_NAME)

        artifact_filename = f"{key}.py"
        contract_filename = f"{key}.json"
        (artifacts_dir / artifact_filename).write_text(artifact_source, encoding="utf-8")
        (contracts_dir / contract_filename).write_text(contract_text, encoding="utf-8")

        content_hashes[key] = hashlib.sha256(
            (artifact_source + "\0" + contract_text).encode("utf-8")
        ).hexdigest()[:16]

        classification = "none"
        if previous_manifest is not None:
            prev_entry = previous_manifest.entries.get(key)
            if prev_entry is not None and previous_package_dir is not None:
                prev_contract_path = previous_package_dir / prev_entry.contract_path
                if prev_contract_path.exists():
                    prev_dict = json.loads(prev_contract_path.read_text(encoding="utf-8"))
                    prev_surface = surface_from_dict(prev_dict.get("surface") or {})
                    result = diff(prev_surface, surface)
                    classification = result.classification
                    if release_type == "patch" and classification in ("major", "minor"):
                        warnings.append(BuildWarning(
                            slot.slot_id,
                            f"declared release type 'patch' but slot {key} classifies as "
                            f"{classification} ({'; '.join(result.reasons) or 'behavior changed'})",
                        ))
                    elif release_type == "minor" and classification == "major":
                        warnings.append(BuildWarning(
                            slot.slot_id,
                            f"declared release type 'minor' but slot {key} classifies as major "
                            f"({'; '.join(result.reasons) or 'behavior changed'})",
                        ))

        entries[key] = ManifestEntry(
            spec_equivalence_key=key,
            slot_id=slot.slot_id,
            artifact_module=f"{ARTIFACTS_DIR}/{artifact_filename}",
            artifact_function=ARTIFACT_FUNCTION_NAME,
            contract_path=f"{CONTRACTS_DIR}/{contract_filename}",
            mode="adaptive",
            classification=classification,
        )

    manifest = Manifest(baseline_hash=compute_baseline_hash(content_hashes), entries=entries)
    (output_dir / MANIFEST_FILENAME).write_text(manifest_to_json(manifest), encoding="utf-8")
    return BuildResult(manifest=manifest, warnings=warnings)
