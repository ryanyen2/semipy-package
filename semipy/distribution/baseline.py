"""Layered portal: baseline version identity (U8, R15/R17).

The baseline is the read-only package data a library ships under
``_semiformal/`` (see ``distribution/runtime.py``, ``distribution/manifest.py``);
the overlay is an ordinary portal in the consuming project's own
``.semiformal/``, produced by the same generate/adapt pipeline a developer
uses. An overlay commit records which baseline it was built against in
``commitment_record["baseline_version"]``; once the installed baseline moves
on (a package upgrade), that commit is stale. U9's floor-gated adapt owns
actually writing the stamp when it adapts -- this module only reads it.
"""
from __future__ import annotations

from typing import Any, Optional

from semipy.distribution.runtime import _load_manifest, find_package_root


def installed_baseline_version(slot: Any) -> Optional[str]:
    """The installed baseline's version (its manifest's ``baseline_hash``) for
    *slot*'s call site, or ``None`` if no package data is installed there --
    e.g. an ordinary developer slot with no shipped baseline at all."""
    call_site_info = getattr(slot, "call_site_info", None) or {}
    source_file = call_site_info.get("filename")
    if not source_file:
        return None
    package_root = find_package_root(source_file)
    if package_root is None:
        return None
    return _load_manifest(package_root).baseline_hash


def is_stale_overlay_commit(commit: Any, installed_baseline_version: Optional[str]) -> bool:
    """A commit is a stale overlay commit if it was built against a baseline
    version that no longer matches what's installed. A commit with no
    ``baseline_version`` stamp at all -- an ordinary developer commit, or one
    made by today's pre-U9 generate/adapt path, which does not yet write the
    stamp -- is never considered stale."""
    if commit is None:
        return False
    stamped = (getattr(commit, "commitment_record", None) or {}).get("baseline_version")
    if stamped is None:
        return False
    return stamped != installed_baseline_version
