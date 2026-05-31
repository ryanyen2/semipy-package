"""Provenance: walk from an applied artifact mutation back to the intent that caused it.

For any ledger event:  artifact mutation  ->  LedgerEvent (when/what applied)
                                          ->  origin commit (the HOW: generated source)
                                          ->  slot spec + change reason (the WHY/WHAT).

This is the why/how/where chain (Buneman/Cheney/Tan) realized end-to-end for
LLM-synthesized effects -- the link no other system has, because semipy co-locates
the generated code's lineage with the effect it produced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from semipy.effects.ledger import get_ledger


@dataclass
class ProvenanceChain:
    event_id: str
    status: str
    targets: list[str] = field(default_factory=list)
    origin_commit_id: str = ""
    decision: str = ""
    spec_text: str = ""        # the WHAT: the user's #> / semi() intent
    reason: str = ""           # the WHY: the change record's reason for that commit
    generated_source: str = ""  # the HOW

    def format(self) -> str:
        bits = [f"effect event {self.event_id[:8]} [{self.status}] -> {', '.join(self.targets) or '(none)'}"]
        if self.origin_commit_id:
            bits.append(f"  commit {self.origin_commit_id[:8]} ({self.decision})")
        if self.spec_text:
            bits.append(f"  spec: {self.spec_text.splitlines()[0][:100]}")
        if self.reason:
            bits.append(f"  why: {self.reason.splitlines()[0][:100]}")
        return "\n".join(bits)


def provenance_for(slot: Any, event_id: Optional[str] = None) -> Optional[ProvenanceChain]:
    """Return the provenance chain for ``event_id`` (or the latest event)."""
    ledger = get_ledger(slot)
    ev = ledger.find(event_id) if event_id else ledger.latest()
    if ev is None:
        return None
    commit = (getattr(slot, "commits", {}) or {}).get(ev.origin_commit_id)
    src = getattr(commit, "generated_source", "") if commit else ""
    decision = getattr(commit, "decision", "") if commit else ""

    spec_text = ""
    snap = getattr(slot, "slot_spec", None)
    if isinstance(snap, dict):
        spec_text = snap.get("spec_text", "") or ""
    if not spec_text and commit is not None:
        spec_text = getattr(commit, "prompt_snapshot", "") or ""

    reason = ""
    if commit is not None:
        cr = getattr(commit, "change_record", {}) or {}
        if isinstance(cr, dict):
            reason = str(cr.get("reason", "") or "")

    targets = sorted({e.target for e in ev.applied_effects})
    return ProvenanceChain(
        event_id=ev.event_id, status=ev.status, targets=targets,
        origin_commit_id=ev.origin_commit_id, decision=decision,
        spec_text=spec_text, reason=reason, generated_source=src,
    )
