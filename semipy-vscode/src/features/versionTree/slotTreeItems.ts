import { ThemeColor, ThemeIcon } from "vscode";
import type { CommitJson, LedgerEventJson } from "../../data/types";
import type { Guarantee, SlotHealth } from "../intelligence/slotInsight";

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

/** Health -> a tinted ThemeIcon for the slot row, matching the gutter glyph. */
export function healthIcon(health: SlotHealth): ThemeIcon {
  switch (health) {
    case "danger":
      return new ThemeIcon("error", new ThemeColor("errorForeground"));
    case "warn":
      return new ThemeIcon("warning", new ThemeColor("charts.yellow"));
    case "effect":
      return new ThemeIcon("circle-outline", new ThemeColor("charts.yellow"));
    default:
      return new ThemeIcon("circle-filled", new ThemeColor("charts.green"));
  }
}

export function guaranteeIcon(g: Guarantee): ThemeIcon {
  if (g.patterns === 0 && g.quarantined > 0) {
    return new ThemeIcon("warning", new ThemeColor("charts.yellow"));
  }
  if (g.kind === "invariant") {
    return new ThemeIcon("shield", new ThemeColor("charts.green"));
  }
  if (g.kind === "metamorphic") {
    return new ThemeIcon("symbol-operator", new ThemeColor("charts.blue"));
  }
  return new ThemeIcon("bookmark", new ThemeColor("charts.green"));
}

export function eventIcon(e: LedgerEventJson): ThemeIcon {
  const status = e.status || "applied";
  if (status === "reverted") {
    return new ThemeIcon("discard");
  }
  if (status === "approval_pending" || status === "shadow") {
    return new ThemeIcon("clock", new ThemeColor("charts.yellow"));
  }
  return new ThemeIcon("zap", new ThemeColor("charts.yellow"));
}

export function eventLabel(e: LedgerEventJson): string {
  const ops = [...new Set((e.applied_effects || []).map((x) => x.op))];
  const targets = [...new Set((e.applied_effects || []).map((x) => x.target).filter(Boolean))];
  const head = targets.length ? targets.join(", ") : (e.event_id || "").slice(0, 8);
  return `${ops.join("/")} ${head}`.trim();
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
