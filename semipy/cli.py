"""CLI for portal maintenance: regenerate, lock, rollback, diagnostics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from semipy._slot_region import expand_zone
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


def _find_slot_region_in_source(
    source_lines: list[str],
    anchor_line_1: int,
) -> tuple[int, int]:
    """Walk up/down from ``anchor_line_1`` while lines are ``#>`` / ``#<`` /
    blank to locate the current slot region. Returns (start1, end1) inclusive.
    """
    n = len(source_lines)
    if n == 0 or anchor_line_1 < 1 or anchor_line_1 > n:
        return (anchor_line_1, anchor_line_1)
    anchor_idx = anchor_line_1 - 1
    start_idx, end_idx = expand_zone(source_lines, anchor_idx, anchor_idx)
    return (start_idx + 1, end_idx + 1)


def cmd_rewind_spec(portal_path: Path, slot_id: str, commit_id: str) -> None:
    portal, _ = _load_portal_explicit(portal_path)
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise SystemExit(f"unknown slot_id {slot_id!r}")
    commit = slot.commits.get(commit_id)
    if commit is None:
        raise SystemExit(f"unknown commit_id {commit_id!r} on slot {slot_id!r}")
    snap = getattr(commit, "source_snapshot", None) or {}
    region_text = snap.get("slot_region_text") if isinstance(snap, dict) else None
    if not region_text:
        sys.stderr.write(
            f"semipy rewind-spec: commit {commit_id[:8]} has no source_snapshot (legacy commit); "
            f"nothing to rewind.\n"
        )
        raise SystemExit(1)

    # Resolve the source file: prefer the snapshot's original, then the slot
    # spec's current source_span.
    source_file = None
    if isinstance(snap, dict):
        source_file = snap.get("source_file") or None
    if not source_file:
        ss = getattr(slot, "slot_spec", None) or {}
        if isinstance(ss, dict):
            span = ss.get("source_span") or ()
            if isinstance(span, (list, tuple)) and len(span) >= 1:
                source_file = span[0]
    if not source_file:
        raise SystemExit("semipy rewind-spec: cannot determine source file for slot")

    src_path = Path(source_file)
    if not src_path.exists():
        raise SystemExit(f"semipy rewind-spec: source file {src_path} not found")

    original_text = src_path.read_text(encoding="utf-8")
    original_lines = original_text.splitlines()
    preserve_trailing_newline = original_text.endswith("\n")

    # Find the CURRENT slot region: start from the slot_spec's source_span line
    # if available; otherwise use the snapshot's recorded start line.
    anchor_line = None
    slot_spec_dict = getattr(slot, "slot_spec", None) or {}
    if isinstance(slot_spec_dict, dict):
        span = slot_spec_dict.get("source_span") or ()
        if isinstance(span, (list, tuple)) and len(span) >= 2:
            try:
                anchor_line = int(span[1])
            except Exception:
                anchor_line = None
    if anchor_line is None:
        anchor_line = int(snap.get("slot_region_start_line") or 1)

    cur_start1, cur_end1 = _find_slot_region_in_source(original_lines, anchor_line)
    new_region_lines = region_text.splitlines()
    new_lines = (
        original_lines[: cur_start1 - 1]
        + new_region_lines
        + original_lines[cur_end1:]
    )
    new_text = "\n".join(new_lines)
    if preserve_trailing_newline and not new_text.endswith("\n"):
        new_text += "\n"
    src_path.write_text(new_text, encoding="utf-8")
    sys.stdout.write(
        f"semipy rewind-spec: rewrote {src_path} lines {cur_start1}-{cur_end1} "
        f"to snapshot of commit {commit_id[:8]}.\n"
    )


def cmd_revert_effect(portal_path: Path, slot_id: str, event_id: str) -> None:
    """Durably revert an applied effect: replay its stored compensations and append
    a ``reverted`` event to the ledger (append-only audit trail), then persist."""
    from semipy.effects.compensate import revert_ledger_event

    portal, cache_dir = _load_portal_explicit(portal_path)
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise SystemExit(f"unknown slot_id {slot_id!r}")
    try:
        count = revert_ledger_event(slot, event_id)
    except KeyError as exc:
        raise SystemExit(str(exc))
    save_portal(cache_dir, portal)
    sys.stdout.write(
        f"semipy revert-effect: replayed {count} compensation(s) for event "
        f"{event_id[:8]} on slot {slot_id[:8]}.\n"
    )


def cmd_quarantine_cases(portal_path: Path, slot_id: str, case_ids: str) -> None:
    """Relax (quarantine) the named contract cases: keep them for audit but stop
    enforcing them. Used by the editor's 'Relax guarantee' action."""
    from semipy.contract.access import quarantine_cases

    portal, cache_dir = _load_portal_explicit(portal_path)
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise SystemExit(f"unknown slot_id {slot_id!r}")
    ids = [c.strip() for c in case_ids.split(",") if c.strip()]
    if not ids:
        raise SystemExit("no case-ids given")
    quarantine_cases(slot, ids, "relaxed from editor")
    save_portal(cache_dir, portal)
    sys.stdout.write(f"semipy quarantine-cases: relaxed {len(ids)} case(s) on slot {slot_id[:8]}.\n")


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

    prw = sub.add_parser(
        "rewind-spec",
        help="Rewrite the slot's #> spec and #< surface in the source file to the commit's snapshot",
    )
    prw.add_argument("--portal", type=Path, required=True)
    prw.add_argument("--slot-id", required=True)
    prw.add_argument("--commit-id", required=True)

    pre = sub.add_parser("revert-effect", help="Revert an applied effect by replaying its compensations")
    pre.add_argument("--portal", type=Path, required=True)
    pre.add_argument("--slot-id", required=True)
    pre.add_argument("--event-id", required=True)

    pq = sub.add_parser("quarantine-cases", help="Relax (quarantine) contract cases by id")
    pq.add_argument("--portal", type=Path, required=True)
    pq.add_argument("--slot-id", required=True)
    pq.add_argument("--case-ids", required=True, help="comma-separated case ids")

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
    elif args.cmd == "rewind-spec":
        cmd_rewind_spec(Path(args.portal), args.slot_id, args.commit_id)
    elif args.cmd == "revert-effect":
        cmd_revert_effect(Path(args.portal), args.slot_id, args.event_id)
    elif args.cmd == "quarantine-cases":
        cmd_quarantine_cases(Path(args.portal), args.slot_id, args.case_ids)
    elif args.cmd == "diagnostics":
        cmd_diagnostics(Path(args.portal).parent)
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
