import type { CommitJson, SlotJson } from "../../data/types";

const LOCK_REF = "__locked__";

/** Active commit: locked ref wins, then newest branch head (matches store._get_active_commit). */
export function activeCommitFromPortalSlot(slot: SlotJson): CommitJson | undefined {
  const locked = slot.refs?.[LOCK_REF];
  if (locked && slot.commits[locked]) {
    return slot.commits[locked];
  }
  let best: CommitJson | undefined;
  let bestTs = -1;
  for (const b of Object.values(slot.branches)) {
    const c = slot.commits[b.head];
    if (c !== undefined && c.timestamp > bestTs) {
      best = c;
      bestTs = c.timestamp;
    }
  }
  if (best !== undefined) {
    return best;
  }
  if (!slot.refs || !slot.commits) {
    return undefined;
  }
  const ids = new Set(
    Object.entries(slot.refs)
      .filter(([k]) => k !== LOCK_REF)
      .map(([, v]) => v),
  );
  const candidates = [...ids].map((id) => slot.commits[id]).filter(Boolean) as CommitJson[];
  if (candidates.length === 0) {
    return undefined;
  }
  return candidates.reduce((a, b) => (a.timestamp >= b.timestamp ? a : b));
}
