import type { CommitJson, SlotJson } from "../../data/types";

/** Same order as semipy.history.walk_history: commit first, then parents. */
export function walkHistoryCommits(slot: SlotJson, commitId: string): CommitJson[] {
  const result: CommitJson[] = [];
  const seen = new Set<string>();
  const stack = [commitId];
  while (stack.length) {
    const cid = stack.pop()!;
    if (seen.has(cid)) {
      continue;
    }
    seen.add(cid);
    const c = slot.commits[cid];
    if (!c) {
      continue;
    }
    result.push(c);
    for (const pid of c.parent_ids) {
      stack.push(pid);
    }
  }
  return result;
}
