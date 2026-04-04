"""CLI for portal maintenance: regenerate, lock, rollback, diagnostics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from semipy.diagnostics_export import _diagnostics_path, _read_entries
from semipy.history.version_lock import lock_slot_to_commit, rollback_slot, unlock_slot
from semipy.library.sketch_store import load_sketch_library
from semipy.store import load_portal, save_portal, write_dispatch_module


def _load_portal_explicit(portal_path: Path):
    data = json.loads(portal_path.read_text(encoding="utf-8"))
    cache_dir = portal_path.parent
    session_id = str(data.get("session_id", ""))
    source_file = str(data.get("source_file", ""))
    module_name = str(data.get("module_name", ""))
    return load_portal(cache_dir, session_id, source_file, module_name), cache_dir


def cmd_regenerate(portal_path: Path, slot_id: str) -> None:
    portal, cache_dir = _load_portal_explicit(portal_path)
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise SystemExit(f"unknown slot_id {slot_id!r}")
    if not isinstance(slot.advisor_state, dict):
        slot.advisor_state = {}
    slot.advisor_state["force_regenerate_next"] = True
    save_portal(cache_dir, portal)


def cmd_lock(portal_path: Path, slot_id: str, commit_id: str) -> None:
    portal, cache_dir = _load_portal_explicit(portal_path)
    lock_slot_to_commit(portal, slot_id, commit_id)
    save_portal(cache_dir, portal)
    sk = load_sketch_library(cache_dir)
    write_dispatch_module(cache_dir, portal, sketch_library=sk)


def cmd_unlock(portal_path: Path, slot_id: str) -> None:
    portal, cache_dir = _load_portal_explicit(portal_path)
    unlock_slot(portal, slot_id)
    save_portal(cache_dir, portal)
    sk = load_sketch_library(cache_dir)
    write_dispatch_module(cache_dir, portal, sketch_library=sk)


def cmd_rollback(portal_path: Path, slot_id: str, commit_id: str) -> None:
    portal, cache_dir = _load_portal_explicit(portal_path)
    rollback_slot(portal, slot_id, commit_id)
    save_portal(cache_dir, portal)
    sk = load_sketch_library(cache_dir)
    write_dispatch_module(cache_dir, portal, sketch_library=sk)


def cmd_diagnostics(cache_dir: Path) -> None:
    path = _diagnostics_path(cache_dir)
    entries = _read_entries(path)
    json.dump({"entries": entries}, sys.stdout, indent=2)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="semipy")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("regenerate", help="Mark slot for regeneration on next execute")
    pr.add_argument("--portal", type=Path, required=True)
    pr.add_argument("--slot-id", required=True)

    pl = sub.add_parser("lock", help="Pin active dispatch to a commit")
    pl.add_argument("--portal", type=Path, required=True)
    pl.add_argument("--slot-id", required=True)
    pl.add_argument("--commit-id", required=True)

    pu = sub.add_parser("unlock", help="Remove commit lock")
    pu.add_argument("--portal", type=Path, required=True)
    pu.add_argument("--slot-id", required=True)

    pb = sub.add_parser("rollback", help="Move default branch head to a commit")
    pb.add_argument("--portal", type=Path, required=True)
    pb.add_argument("--slot-id", required=True)
    pb.add_argument("--commit-id", required=True)

    pd = sub.add_parser("diagnostics", help="Print diagnostics.json entries")
    pd.add_argument("--portal", type=Path, required=True)

    args = p.parse_args(argv)

    if args.cmd == "regenerate":
        cmd_regenerate(Path(args.portal), args.slot_id)
    elif args.cmd == "lock":
        cmd_lock(Path(args.portal), args.slot_id, args.commit_id)
    elif args.cmd == "unlock":
        cmd_unlock(Path(args.portal), args.slot_id)
    elif args.cmd == "rollback":
        cmd_rollback(Path(args.portal), args.slot_id, args.commit_id)
    elif args.cmd == "diagnostics":
        cmd_diagnostics(Path(args.portal).parent)
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
