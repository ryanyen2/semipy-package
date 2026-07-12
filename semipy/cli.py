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


def _dispute_decision_set(slot):
    """Return the slot's decision set and an open decision to assert against,
    synthesizing a placeholder single-branch decision when the slot has no
    surfaced fork yet.

    ``dispute`` (U5) has to land through the same ``assert_property`` plumbing
    ``assert-decision`` uses, but that plumbing hard-requires a pre-existing
    open ``Decision`` in the slot's decision set. Most disputed slots never
    surfaced a fork (there's nothing ambiguous about them -- the reporter just
    thinks the output is wrong), so a real fork rarely exists to assert
    against. Rather than changing ``assert_property``'s contract, mint a
    single-branch placeholder decision here when needed; ``assert_property``
    then records the disputed property as a contract case exactly as it does
    for a real fork.
    """
    from semipy.decisions.model import Branch, Decision, DecisionSet
    from semipy.decisions.persistence import attach_decision_set, decision_set_for

    dset = decision_set_for(slot) or DecisionSet(slot_id=getattr(slot, "slot_id", "") or "")
    decision = next((d for d in dset.decisions if d.is_open), None)
    if decision is None:
        decision = Decision(
            germ="dispute", axis_label="disputed behavior", branches=[Branch(fate_label="disputed")]
        )
        dset.decisions.append(decision)
        attach_decision_set(slot, dset)
    return dset, decision


def cmd_dispute(portal_path: Path, slot_id: str, property_text: str, as_json: bool) -> None:
    from semipy.decisions.resolve import assert_property

    portal, cache_dir = _load_portal_explicit(portal_path)
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise SystemExit(f"unknown slot_id {slot_id!r}")
    dset, decision = _dispute_decision_set(slot)
    # No stored candidate can satisfy a fresh dispute (the placeholder decision
    # has none, and a real fork's alternates were already considered); it
    # always signals a targeted regeneration, same as an unresolved assert.
    res = assert_property(
        slot,
        dset,
        decision_id=decision.decision_id,
        property_text=property_text,
        satisfies=lambda _cid: False,
        usage_id=slot_id,
    )
    save_portal(cache_dir, portal)
    if res.commit_id:
        write_dispatch_module(cache_dir, portal, sketch_library=load_sketch_library(cache_dir))
    if as_json:
        json.dump(
            {"contract_case_id": res.contract_case_id, "regen_needed": res.regen_needed,
             "commit_id": res.commit_id, "decision_id": decision.decision_id},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return
    sys.stdout.write(
        f"semipy dispute: recorded property as case {res.contract_case_id}; "
        "regenerates against it on next call.\n"
    )


def _render_surface(surface) -> None:
    from semipy.contract.surface import SCHEMA_VERSION

    out = sys.stdout
    active = surface.active_cases()
    n_super = sum(1 for c in surface.cases.values() if c.get("status") == "superseded")
    n_quar = sum(1 for c in surface.cases.values() if c.get("status") == "quarantined")
    out.write(f"contract surface  slot {surface.slot_id[:12]}  (schema v{SCHEMA_VERSION})\n")
    if surface.spec_text:
        out.write(f"  spec: {surface.spec_text}\n")
    if surface.expected_type:
        out.write(f"  type: {surface.expected_type}\n")
    out.write(f"  scope: {surface.scope_predicate_ref or '(none minted yet)'}\n")
    if surface.certified and surface.certificate:
        cert = surface.certificate
        out.write(
            f"  CERTIFIED: freeze licensed (epsilon={cert.get('epsilon')}, "
            f"delta={cert.get('delta')}, held-out={cert.get('held_out_pass_fraction')})\n"
        )
    else:
        out.write(
            "  UNCERTIFIED: no licensed freeze -- partial contract "
            "(active cases/relations are checkable; whole-slot output not frozen)\n"
        )
        for r in (surface.certificate or {}).get("refusal_reasons", []):
            out.write(f"    refusal: {r}\n")
    if surface.regimes:
        out.write(f"  regimes ({len(surface.regimes)}):\n")
        for g in surface.regimes:
            fb = " [fallback]" if g.get("is_fallback") else ""
            out.write(f"    - {g.get('predicate_source', '')}{fb}\n")
    if surface.relations:
        out.write(f"  relations: {', '.join(surface.relations)}\n")
    out.write(f"  cases: {len(active)} active, {n_super} superseded, {n_quar} quarantined\n")
    for c in active:
        label = c.get("invariant") or c.get("relation") or c.get("kind") or "case"
        ship = "ship" if c.get("ship") else "no-ship"
        reason = (c.get("reason") or "").replace("\n", " ")
        if len(reason) > 70:
            reason = reason[:67] + "..."
        out.write(f"    - [{label}] {c.get('case_id', '')[:12]} ({ship}) {reason}\n")


def cmd_contract_show(portal_path: Path, slot_id: str, as_json: bool) -> None:
    from semipy.contract.surface import ContractSurface, surface_to_json

    portal, _ = _load_portal_explicit(portal_path)
    slot = portal.slots.get(slot_id)
    if slot is None:
        raise SystemExit(f"unknown slot_id {slot_id!r}")
    surface = ContractSurface.from_slot(slot)
    if as_json:
        sys.stdout.write(surface_to_json(surface))
        return
    _render_surface(surface)


def cmd_contract_diff(old_path: Path, new_path: Path, as_json: bool) -> None:
    from semipy.contract.surface import ContractSchemaError, diff, surface_from_json

    try:
        old = surface_from_json(old_path.read_text(encoding="utf-8"))
        new = surface_from_json(new_path.read_text(encoding="utf-8"))
    except ContractSchemaError as exc:
        raise SystemExit(f"semipy contract diff: {exc}")
    result = diff(old, new)
    if as_json:
        json.dump(
            {
                "classification": result.classification,
                "added_cases": result.added_cases,
                "superseded_cases": result.superseded_cases,
                "added_regimes": result.added_regimes,
                "scope_changed": result.scope_changed,
                "certificate_invalidated": result.certificate_invalidated,
                "reasons": result.reasons,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return
    sys.stdout.write(f"contract diff: {result.classification}\n")
    for r in result.reasons:
        sys.stdout.write(f"  {r}\n")
    if result.classification == "none":
        sys.stdout.write("  (no behavioral change)\n")


def _resolve_slot_id_for_why(portal, *, slot_id: str | None, file_line: str | None) -> str:
    """Resolve either an explicit slot_id or a ``path:line`` reference (R8) to a
    slot_id, reusing ``_slot_rows``'s file/line resolution. When no slot sits
    exactly on ``line``, the nearest slot in that file is used (an editor click
    lands inside a slot's region, not necessarily on its first line)."""
    if slot_id:
        return slot_id
    if not file_line:
        raise SystemExit("semipy why: pass --slot-id or --file-line")
    file_part, _, line_part = file_line.rpartition(":")
    if not file_part or not line_part.isdigit():
        raise SystemExit(f"semipy why: --file-line must be path:line, got {file_line!r}")
    target_line = int(line_part)
    rows = _slot_rows(portal, file_part)
    if not rows:
        raise SystemExit(f"semipy why: no slot found in file {file_part!r}")
    best = min(rows, key=lambda r: abs((r["line"] or 0) - target_line))
    return best["slot_id"]


def _profile_distance(a: dict, b: dict) -> int:
    """Number of free variables whose structural profile differs between two
    profile dicts -- the nearest-case distance (R7): 0 for an identical profile,
    rising with each variable whose shape/kind diverges."""
    keys = set(a) | set(b)
    return sum(
        1 for k in keys
        if json.dumps(a.get(k), sort_keys=True, default=str) != json.dumps(b.get(k), sort_keys=True, default=str)
    )


def _nearest_case(cases: list[dict], target_profile: dict) -> tuple[dict | None, int | None]:
    from semipy.runtime_fingerprint import compute_input_profile

    best_case, best_dist = None, None
    for c in cases:
        try:
            profile = compute_input_profile(c.get("input_sample") or {})
        except Exception:
            continue
        dist = _profile_distance(target_profile, profile)
        if best_dist is None or dist < best_dist:
            best_case, best_dist = c, dist
    return best_case, best_dist


def _why_answer(slot, *, input_values: dict | None) -> dict:
    """Assemble the extensional explanation for a slot (R7): spec, active commit
    + decision, certificate + scope verdict for ``input_values``, nearest case,
    regimes, and the certified/uncertified boundary. Reads only what is already
    stored on the slot -- no generated source, no model call."""
    from semipy.contract.surface import ContractSurface
    from semipy.kernel.guard import ScopePredicate
    from semipy.runtime_fingerprint import compute_input_profile
    from semipy.store import _get_active_commit

    surface = ContractSurface.from_slot(slot)
    active = _get_active_commit(slot)
    commit_id = getattr(active, "commit_id", "") or ""
    decision = getattr(active, "decision", "") or ""

    adv = getattr(slot, "advisor_state", None) or {}
    scope_dict = (adv.get("scope_predicates") or {}).get(commit_id)
    scope_source = None
    scope_verdict = None
    if scope_dict:
        predicate = ScopePredicate.from_dict(scope_dict)
        scope_source = predicate.source
        if input_values is not None:
            check = predicate.check(compute_input_profile(input_values))
            scope_verdict = {
                "in_scope": check.in_scope,
                "violated": check.violated,
                "violated_var": check.violated_var,
            }

    nearest_case = None
    if input_values is not None:
        case, dist = _nearest_case(list(surface.cases.values()), compute_input_profile(input_values))
        if case is not None:
            nearest_case = {
                "case_id": case.get("case_id", ""),
                "kind": case.get("kind", ""),
                "status": case.get("status", ""),
                "distance": dist,
            }

    return {
        "slot_id": surface.slot_id,
        "spec_text": surface.spec_text,
        "active_commit": commit_id,
        "decision": decision,
        "certified": surface.certified,
        "certificate": surface.certificate,
        "scope_source": scope_source,
        "scope_verdict": scope_verdict,
        "nearest_case": nearest_case,
        "regimes": surface.regimes,
    }


def _render_why(answer: dict) -> None:
    out = sys.stdout
    out.write(f"why: slot {answer['slot_id'][:12]}\n")
    if answer["spec_text"]:
        out.write(f"  spec: {answer['spec_text']}\n")
    out.write(
        f"  active commit: {(answer['active_commit'] or '(none)')[:8]}  "
        f"decision: {answer['decision'] or '(none)'}\n"
    )
    if answer["certified"] and answer["certificate"]:
        cert = answer["certificate"]
        out.write(
            f"  CERTIFIED: freeze licensed (epsilon={cert.get('epsilon')}, delta={cert.get('delta')})\n"
        )
    else:
        out.write(
            "  UNCERTIFIED: no licensed freeze -- partial contract "
            "(active cases/relations are checkable; whole-slot output not frozen)\n"
        )
    out.write(f"  scope: {answer['scope_source'] or '(none minted yet)'}\n")
    verdict = answer["scope_verdict"]
    if verdict is not None:
        if verdict["in_scope"]:
            out.write("  input: IN SCOPE\n")
        else:
            out.write(f"  input: OUT OF SCOPE (violated: {verdict['violated']})\n")
    nearest = answer["nearest_case"]
    if nearest is not None:
        out.write(
            f"  nearest case: {nearest['case_id'][:12]} "
            f"({nearest['kind']}, {nearest['status']}, distance={nearest['distance']})\n"
        )
    if answer["regimes"]:
        out.write(f"  regimes ({len(answer['regimes'])}):\n")
        for g in answer["regimes"]:
            out.write(f"    - {g.get('predicate_source', '')}\n")


def cmd_build(
    portal_path: Path,
    output_dir: Path,
    previous_dir: Path | None,
    release_type: str | None,
) -> None:
    """``semipy build``: distill a portal into consumer package data (U6)."""
    from semipy.distribution.build import build_package_data

    portal, _ = _load_portal_explicit(portal_path)
    result = build_package_data(
        portal, output_dir, previous_package_dir=previous_dir, release_type=release_type,
    )
    for w in result.warnings:
        sys.stderr.write(f"warning: slot {w.slot_id}: {w.message}\n")
    sys.stdout.write(
        f"built {len(result.manifest.entries)} slot(s) -> {output_dir} "
        f"(baseline_hash={result.manifest.baseline_hash})\n"
    )


def cmd_why(
    portal_path: Path,
    slot_id: str | None,
    file_line: str | None,
    input_json: str | None,
    as_json: bool,
) -> None:
    from semipy.diagnostics_export import export_scope_deopt

    portal, cache_dir = _load_portal_explicit(portal_path)
    resolved_id = _resolve_slot_id_for_why(portal, slot_id=slot_id, file_line=file_line)
    slot = portal.slots.get(resolved_id)
    if slot is None:
        raise SystemExit(f"unknown slot_id {resolved_id!r}")

    input_values = None
    if input_json:
        try:
            input_values = json.loads(input_json)
        except Exception as exc:
            raise SystemExit(f"semipy why: --input must be a JSON object: {exc}")
        if not isinstance(input_values, dict):
            raise SystemExit("semipy why: --input must be a JSON object")

    answer = _why_answer(slot, input_values=input_values)

    verdict = answer["scope_verdict"]
    if verdict is not None and not verdict["in_scope"]:
        ci = slot.call_site_info or {}
        spec = slot.slot_spec or {}
        span = spec.get("source_span") if isinstance(spec, dict) else None
        source_file = ci.get("filename") or (span[0] if isinstance(span, (list, tuple)) and span else "")
        lineno = ci.get("lineno") or (span[1] if isinstance(span, (list, tuple)) and len(span) >= 2 else 0)
        export_scope_deopt(
            cache_dir, resolved_id, verdict["violated"] or "",
            source_file=str(source_file), source_line_start=int(lineno or 0), source_line_end=int(lineno or 0),
        )

    if as_json:
        json.dump(answer, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    _render_why(answer)


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

    pdi = sub.add_parser(
        "dispute",
        help="Record a disputed property as a contract case (assert-decision, without requiring a surfaced fork)",
    )
    pdi.add_argument("--portal", type=Path, required=True)
    pdi.add_argument("--slot-id", required=True)
    pdi.add_argument("--property", required=True, dest="property_text", help="natural-language property the result must satisfy")
    pdi.add_argument("--json", action="store_true", help="emit JSON")

    pcon = sub.add_parser("contract", help="Inspect and diff slot contract surfaces")
    csub = pcon.add_subparsers(dest="contract_cmd", required=True)
    pcs = csub.add_parser("show", help="Render a slot's contract surface (certified/uncertified boundary, cases, scope)")
    pcs.add_argument("--portal", type=Path, required=True)
    pcs.add_argument("--slot-id", required=True)
    pcs.add_argument("--json", action="store_true", help="emit the serialized surface JSON")
    pcd = csub.add_parser("diff", help="Classify the behavioral-semver delta between two surface JSON files")
    pcd.add_argument("--old", type=Path, required=True, help="path to the baseline surface JSON")
    pcd.add_argument("--new", type=Path, required=True, help="path to the new surface JSON")
    pcd.add_argument("--json", action="store_true", help="emit JSON")

    pbu = sub.add_parser(
        "build",
        help="Distill a portal into consumer-facing package data (_semiformal/: manifest, artifacts, floor-filtered contracts)",
    )
    pbu.add_argument("--portal", type=Path, required=True)
    pbu.add_argument("--output", type=Path, required=True, help="output _semiformal/ directory")
    pbu.add_argument(
        "--previous", type=Path, default=None,
        help="a prior build's _semiformal/ directory, for behavioral-semver classification (KTD-8)",
    )
    pbu.add_argument(
        "--release-type", choices=["major", "minor", "patch"], default=None,
        help="declared release type; warns on a KTD-8 classification mismatch (U12 owns enforcement)",
    )

    pw = sub.add_parser(
        "why",
        help="Explain a slot: spec, active decision, certificate + scope verdict, nearest case",
    )
    pw.add_argument("--portal", type=Path, required=True)
    pw_target = pw.add_mutually_exclusive_group(required=True)
    pw_target.add_argument("--slot-id")
    pw_target.add_argument("--file-line", help="path:line to resolve to a slot")
    pw.add_argument("--input", dest="input_json", default=None, help="JSON object of runtime values to test scope membership / find the nearest case")
    pw.add_argument("--json", action="store_true")

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
    elif args.cmd == "dispute":
        cmd_dispute(Path(args.portal), args.slot_id, args.property_text, args.json)
    elif args.cmd == "contract":
        if args.contract_cmd == "show":
            cmd_contract_show(Path(args.portal), args.slot_id, args.json)
        elif args.contract_cmd == "diff":
            cmd_contract_diff(Path(args.old), Path(args.new), args.json)
        else:
            raise SystemExit(2)
    elif args.cmd == "why":
        cmd_why(Path(args.portal), args.slot_id, args.file_line, args.input_json, args.json)
    elif args.cmd == "build":
        cmd_build(
            Path(args.portal), Path(args.output),
            Path(args.previous) if args.previous else None, args.release_type,
        )
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
