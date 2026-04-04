import { ThemeIcon } from "vscode";
import type { CommitJson } from "../../data/types";

export function decisionIcon(decision: string): ThemeIcon {
  const d = (decision || "").toUpperCase();
  if (d === "GENERATE") {
    return new ThemeIcon("git-commit");
  }
  if (d === "ADAPT") {
    return new ThemeIcon("git-merge");
  }
  if (d === "REUSE" || d === "reuse") {
    return new ThemeIcon("link");
  }
  if (d === "INSTANTIATE" || d === "instantiate") {
    return new ThemeIcon("puzzle");
  }
  return new ThemeIcon("git-commit");
}

export function formatCommitLabel(c: CommitJson): string {
  const id = c.commit_id.slice(0, 8);
  const msg = (c.message || "").replace(/\s+/g, " ").slice(0, 48);
  const ts = c.timestamp
    ? new Date(c.timestamp * 1000).toLocaleString()
    : "";
  return `${id} | ${c.decision || "?"} | ${msg}${ts ? ` | ${ts}` : ""}`;
}

export function truncateSpecPreview(spec: string, n = 60): string {
  const t = spec.replace(/\s+/g, " ").trim();
  if (t.length <= n) {
    return t;
  }
  return t.slice(0, n - 1) + "…";
}
