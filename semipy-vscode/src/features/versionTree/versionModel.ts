/**
 * Stable, rigorous version numbering for a slot's implementations.
 *
 * The package stores implementations as an append-only Merkle DAG of commits
 * (children created strictly after parents). We present them to the user as
 * v1..vN ordered by (timestamp, commit_id): a total order that
 *   - never renumbers existing versions when a new one is appended (a new commit
 *     has the largest timestamp -> becomes vN+1),
 *   - is deterministic across reloads (commit_id breaks the practically-impossible
 *     timestamp tie),
 *   - respects ancestry (a child's wall-clock timestamp >= its parent's).
 *
 * "Active" = the version that actually runs. This mirrors the package exactly:
 * a locked commit wins (`refs.__locked__`), otherwise the newest branch head.
 * Checkout is implemented via LOCK, because the package's RoutingPolicy
 * short-circuits a locked slot to `REUSE(locked commit)` (precedence #2) and
 * `write_dispatch_module` emits the locked commit -- so a checked-out version is
 * guaranteed to run unchanged. `rollback` (move a branch head) is NOT used: it is
 * not authoritative when another branch has a newer head.
 */
import type { CommitJson, SlotJson } from "../../data/types";
import { activeCommitFromPortalSlot } from "../splitEditor/portalCommit";

export const LOCK_REF = "__locked__";

export interface SlotVersion {
  commit: CommitJson;
  version: number; // 1-based, stable
  isActive: boolean; // currently runs
  isLocked: boolean; // pinned via refs.__locked__
}

function compareCommits(a: CommitJson, b: CommitJson): number {
  if (a.timestamp !== b.timestamp) {
    return a.timestamp - b.timestamp;
  }
  return a.commit_id < b.commit_id ? -1 : a.commit_id > b.commit_id ? 1 : 0;
}

export function orderedVersions(slot: SlotJson): SlotVersion[] {
  const commits = Object.values(slot.commits || {});
  if (commits.length === 0) {
    return [];
  }
  const sorted = [...commits].sort(compareCommits);
  const activeId = activeCommitFromPortalSlot(slot)?.commit_id;
  const lockedId = slot.refs?.[LOCK_REF];
  return sorted.map((commit, i) => ({
    commit,
    version: i + 1,
    isActive: commit.commit_id === activeId,
    isLocked: !!lockedId && commit.commit_id === lockedId,
  }));
}

export function activeVersion(slot: SlotJson): SlotVersion | undefined {
  const versions = orderedVersions(slot);
  if (versions.length === 0) {
    return undefined;
  }
  return versions.find((v) => v.isActive) ?? versions[versions.length - 1];
}

/** Version number for a specific commit (1-based), or 0 if not found. */
export function versionOfCommit(slot: SlotJson, commitId: string): number {
  return orderedVersions(slot).find((v) => v.commit.commit_id === commitId)?.version ?? 0;
}

/** Compact CodeLens label, e.g. "v2/3" or "v2/3 · pinned". */
export function versionLensLabel(slot: SlotJson): string | undefined {
  const versions = orderedVersions(slot);
  if (versions.length === 0) {
    return undefined;
  }
  const active = activeVersion(slot);
  if (!active) {
    return undefined;
  }
  const pinned = versions.some((v) => v.isLocked) ? " · pinned" : "";
  return `v${active.version}/${versions.length}${pinned}`;
}
