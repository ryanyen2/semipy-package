"""CLI for portal maintenance: regenerate, lock, rollback, diagnostics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from semipy._slot_region import expand_zone
from semipy.diagnostics_export import _diagnostics_path, _read_entries
from semipy.history.version_lock import (
    lock_slot_to_commit,
    reset_slot,
    reset_version,
    rollback_slot,
    unlock_slot,
)
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


def _slot_rows(portal, file_filter: str | None) -> list[dict]:
    from semipy.store import _get_active_commit

    rows: list[dict] = []
    for sid, slot in portal.slots.items():
        ci = slot.call_site_info or {}
        spec = slot.slot_spec or {}
        span = spec.get("source_span") if isinstance(spec, dict) else None
        filename = ci.get("filename") or (span[0] if isinstance(span, (list, tuple)) and span else "")
        lineno = ci.get("lineno")
        if lineno is None and isinstance(span, (list, tuple)) and len(span) >= 2:
            lineno = span[1]
        func = ci.get("func_qualname") or ""
        spec_text = (spec.get("spec_text") if isinstance(spec, dict) else "") or ""
        spec_text = spec_text.replace("\n", " ").strip()
        if len(spec_text) > 80:
            spec_text = spec_text[:77] + "..."
        if file_filter:
            same_name = Path(str(filename)).name == Path(file_filter).name
            if file_filter not in str(filename) and not same_name:
                continue
        active = _get_active_commit(slot)
        rows.append(
            {
                "slot_id": sid,
                "file": str(filename),
                "line": lineno,
                "func": func,
                "versions": len(slot.commits),
                "decision": getattr(active, "decision", "") if active is not None else "(none)",
                "active_commit": (getattr(active, "commit_id", "") or "")[:8] if active is not None else "",
                "spec": spec_text,
            }
        )
    rows.sort(key=lambda r: (str(r["file"]), r["line"] or 0))
    return rows


def cmd_slots(portal_path: Path, file_filter: str | None, as_json: bool) -> None:
    portal, _ = _load_portal_explicit(portal_path)
    rows = _slot_rows(portal, file_filter)
    if as_json:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    if not rows:
        sys.stdout.write("(no slots)\n")
        return
    for r in rows:
        name = Path(r["file"]).name or r["file"]
        sys.stdout.write(
            f"{r['slot_id'][:12]}  {name}:{r['line']}  {r['func']}  "
            f"v{r['versions']} {r['decision']} {r['active_commit']}\n    {r['spec']}\n"
        )


def cmd_reset_slot(portal_path: Path, slot_id: str) -> None:
    portal, cache_dir = _load_portal_explicit(portal_path)
    try:
        reset_slot(portal, slot_id)
    except KeyError as exc:
        raise SystemExit(str(exc))
    save_portal(cache_dir, portal)
    write_dispatch_module(cache_dir, portal, sketch_library=load_sketch_library(cache_dir))
    sys.stdout.write(f"semipy reset-slot: cleared slot {slot_id[:8]}; next call regenerates.\n")


def cmd_reset_version(portal_path: Path, slot_id: str, commit_id: str) -> None:
    portal, cache_dir = _load_portal_explicit(portal_path)
    try:
        reset_version(portal, slot_id, commit_id)
    except KeyError as exc:
        raise SystemExit(str(exc))
    save_portal(cache_dir, portal)
    write_dispatch_module(cache_dir, portal, sketch_library=load_sketch_library(cache_dir))
    sys.stdout.write(
        f"semipy reset-version: deleted version {commit_id[:8]} from slot {slot_id[:8]}.\n"
    )


def _decision_set_or_exit(slot, slot_id: str):
    from semipy.decisions.persistence import decision_set_for

    dset = decision_set_for(slot)
    if dset is None or dset.is_empty():
        raise SystemExit(f"slot {slot_id[:8]} has no surfaced decisions")
    return dset


def cmd_pick_decision(
    portal_path: Path, slot_id: str, decision_id: str, fate: str, as_json: bool
) -> None:
    from semipy.decisions.resolve import DecisionResolveError, pick_branch

    portal, cache_dir = _load_portal_explicit(portal_path)
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise SystemExit(f"unknown slot_id {slot_id!r}")
    dset = _decision_set_or_exit(slot, slot_id)
    try:
        res = pick_branch(slot, dset, decision_id=decision_id, fate_label=fate, usage_id=slot_id)
    except DecisionResolveError as exc:
        raise SystemExit(str(exc))
    save_portal(cache_dir, portal)
    write_dispatch_module(cache_dir, portal, sketch_library=load_sketch_library(cache_dir))
    if as_json:
        json.dump(
            {"commit_id": res.commit_id, "candidate_id": res.candidate_id, "spec_clause": res.spec_clause},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return
    sys.stdout.write(
        f"semipy pick-decision: {fate!r} -> commit {res.commit_id[:8]}; clause: {res.spec_clause}\n"
    )


def cmd_assert_decision(
    portal_path: Path, slot_id: str, decision_id: str, property_text: str, as_json: bool
) -> None:
    from semipy.decisions.resolve import DecisionResolveError, assert_property

    portal, cache_dir = _load_portal_explicit(portal_path)
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise SystemExit(f"unknown slot_id {slot_id!r}")
    dset = _decision_set_or_exit(slot, slot_id)
    try:
        # No automatic candidate verification at the CLI layer: record the property
        # as a contract case and signal a targeted regeneration. An LLM/execution
        # metamorphic check over stored candidates is a follow-up.
        res = assert_property(
            slot,
            dset,
            decision_id=decision_id,
            property_text=property_text,
            satisfies=lambda _cid: False,
            usage_id=slot_id,
        )
    except DecisionResolveError as exc:
        raise SystemExit(str(exc))
    save_portal(cache_dir, portal)
    if res.commit_id:
        write_dispatch_module(cache_dir, portal, sketch_library=load_sketch_library(cache_dir))
    if as_json:
        json.dump(
            {"contract_case_id": res.contract_case_id, "regen_needed": res.regen_needed,
             "commit_id": res.commit_id},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return
    tail = "regenerates against it on next call" if res.regen_needed else f"satisfied -> commit {(res.commit_id or '')[:8]}"
    sys.stdout.write(
        f"semipy assert-decision: recorded property as case {res.contract_case_id}; {tail}.\n"
    )


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

    ps = sub.add_parser("slots", help="List slots in a portal (file:line, versions, decision)")
    ps.add_argument("--portal", type=Path, required=True)
    ps.add_argument("--file", default=None, help="filter to slots whose source file matches")
    ps.add_argument("--json", action="store_true", help="emit JSON")

    prs = sub.add_parser("reset-slot", help="Clear a slot so the next call regenerates fresh")
    prs.add_argument("--portal", type=Path, required=True)
    prs.add_argument("--slot-id", required=True)

    prv = sub.add_parser("reset-version", help="Delete a single version (commit) from a slot")
    prv.add_argument("--portal", type=Path, required=True)
    prv.add_argument("--slot-id", required=True)
    prv.add_argument("--commit-id", required=True)

    pd = sub.add_parser("diagnostics", help="Print diagnostics.json entries")
    pd.add_argument("--portal", type=Path, required=True)

    ppd = sub.add_parser("pick-decision", help="Resolve a surfaced fork by picking a fate (LLM-free)")
    ppd.add_argument("--portal", type=Path, required=True)
    ppd.add_argument("--slot-id", required=True)
    ppd.add_argument("--decision-id", required=True)
    ppd.add_argument("--fate", required=True, help="the fate_label to pick")
    ppd.add_argument("--json", action="store_true", help="emit JSON")

    pad = sub.add_parser("assert-decision", help="Resolve a fork by asserting a property (records a contract case)")
    pad.add_argument("--portal", type=Path, required=True)
    pad.add_argument("--slot-id", required=True)
    pad.add_argument("--decision-id", required=True)
    pad.add_argument("--property", required=True, dest="property_text", help="natural-language property the result must satisfy")
    pad.add_argument("--json", action="store_true", help="emit JSON")

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
    elif args.cmd == "slots":
        cmd_slots(Path(args.portal), args.file, args.json)
    elif args.cmd == "reset-slot":
        cmd_reset_slot(Path(args.portal), args.slot_id)
    elif args.cmd == "reset-version":
        cmd_reset_version(Path(args.portal), args.slot_id, args.commit_id)
    elif args.cmd == "diagnostics":
        cmd_diagnostics(Path(args.portal).parent)
    elif args.cmd == "pick-decision":
        cmd_pick_decision(Path(args.portal), args.slot_id, args.decision_id, args.fate, args.json)
    elif args.cmd == "assert-decision":
        cmd_assert_decision(
            Path(args.portal), args.slot_id, args.decision_id, args.property_text, args.json
        )
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
