import type { CommitJson, SlotJson } from "../../data/types";

/** Newest branch-head commit across branches (matches store._get_active_commit idea). */
export function activeCommitFromPortalSlot(slot: SlotJson): CommitJson | undefined {
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
  const ids = new Set(Object.values(slot.refs));
  const candidates = [...ids].map((id) => slot.commits[id]).filter(Boolean) as CommitJson[];
  if (candidates.length === 0) {
    return undefined;
  }
  return candidates.reduce((a, b) => (a.timestamp >= b.timestamp ? a : b));
}
