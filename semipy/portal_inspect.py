"""Helpers to print portal / slot summaries for notebooks and debugging."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from semipy.session_anchor import resolve_project
from semipy.store import load_portal
from semipy.types import module_name_for_project, session_id_for_project


def _head_commit_for_slot(slot: Any) -> Any | None:
    commits = getattr(slot, "commits", None) or {}
    branches = getattr(slot, "branches", None) or {}
    default = getattr(slot, "default_branch", "main")
    branch = branches.get(default)
    if branch is not None:
        c = commits.get(branch.head)
        if c is not None:
            return c
    if not commits:
        return None
    return max(commits.values(), key=lambda x: getattr(x, "timestamp", 0.0))


def print_portal_resolution_summary(*, cache_dir: Path, session_anchor: str) -> None:
    """
    Load the portal for a stable session anchor and print each slot's head commit,
    decision, and ``runtime_input_fingerprint`` (when present).

    ``session_anchor`` is a source file (or directory) inside the project; the
    project portal is resolved the same way the runtime does (one portal per
    project = the folder rooted at the nearest ``.semiformal/``).
    """
    resolved_cache_dir, project_root = resolve_project(str(session_anchor), Path(cache_dir))
    session_id = session_id_for_project(project_root)
    module_name = module_name_for_project(project_root)
    portal = load_portal(resolved_cache_dir, session_id, str(project_root), module_name)
    print(
        f"session_id={session_id} module={module_name} "
        f"project={project_root} slots={len(portal.slots)}"
    )
    for sid, sl in portal.slots.items():
        head = _head_commit_for_slot(sl)
        short = sid[:8] + "..." if len(sid) > 8 else sid
        if head is None:
            print(f"\n--- slot_id={short} (no commits)")
            continue
        rfp = getattr(head, "runtime_input_fingerprint", "") or ""
        rfp_disp = rfp if rfp else "(none; legacy or pre-fingerprint)"
        spec_snap = getattr(sl, "slot_spec", None) or {}
        preview = ""
        if isinstance(spec_snap, dict):
            st = (spec_snap.get("spec_text") or "").replace("\n", " ")
            preview = st[:120] + ("..." if len(st) > 120 else "")
        ci = getattr(sl, "call_site_info", None) or {}
        fn = ci.get("func_qualname", "")
        ln = ci.get("lineno", 0)
        print(f"\n--- slot_id={short} line={ln} func={fn!r}")
        print(
            f"  head_commit={head.commit_id[:8]} decision={head.decision} "
            f"runtime_input_fingerprint={rfp_disp}"
        )
        if preview:
            print(f"  spec_preview={preview!r}")
        # Behavioral contract: active/superseded counts and head-commit change record.
        try:
            from semipy.contract.access import get_contract
            from semipy.contract.change import change_record_from_dict

            contract = get_contract(sl)
            n_active = len(contract.active())
            n_superseded = len(contract.superseded())
            n_quarantined = len(contract.quarantined())
            if contract.cases:
                print(
                    f"  contract={n_active} active / {n_superseded} superseded "
                    f"/ {n_quarantined} quarantined"
                )
            cr = change_record_from_dict(getattr(head, "change_record", {}) or {})
            if cr.reason or cr.effect_diff or cr.n_compared:
                intended = len(cr.effect_diff) - cr.unintended_count
                reason_line = (cr.reason or "").splitlines()[0][:100] if cr.reason else ""
                bits = []
                if reason_line:
                    bits.append(f"reason={reason_line!r}")
                bits.append(f"effect=+{intended} changed, {cr.unintended_count} unintended")
                print("  change: " + " | ".join(bits))
        except Exception:
            pass

        # Effect ledger: applied/reverted counts + provenance for the latest event.
        try:
            from semipy.effects.ledger import get_ledger
            from semipy.effects.provenance import provenance_for

            ledger = get_ledger(sl)
            if ledger.events:
                print(
                    f"  ledger={len(ledger.applied())} applied / "
                    f"{len(ledger.reverted())} reverted"
                )
                chain = provenance_for(sl)
                if chain is not None:
                    line = f"  last effect: {', '.join(chain.targets) or '(none)'} " \
                           f"[{chain.status}] by commit {chain.origin_commit_id[:8]}"
                    if chain.reason:
                        line += f"  why={chain.reason.splitlines()[0][:80]!r}"
                    print(line)
        except Exception:
            pass
