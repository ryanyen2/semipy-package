/**
 * Decision insight: turn a portal Slot's `decision_set` into the small set of
 * facts the surfacing UI needs. Pure + unit-testable; no `vscode` import.
 *
 * A "decision" is a fork the model made silently while generating this slot
 * (e.g. skip nulls vs count them as zero). The render contract mirrors
 * `semipy/decisions/model.py` via `DecisionSetJson` in `../../data/types`.
 *
 * Restraint (mirrors slotInsight's minimum-set rule): if a slot has no OPEN
 * decisions, the UI shows nothing. A resolved fork is silent.
 */
import type { DecisionBranchJson, DecisionJson, SlotJson } from "../../data/types";

/** Typographic glyph for an open fork (the FORK glyph; no emoji, matches slotInsight). */
export const DECISION_GLYPH = "⎇"; // ⎇

/** Open decisions for a slot, highest-consequence first. Empty when none. */
export function openDecisionsFor(slot: SlotJson | undefined): DecisionJson[] {
  const ds = slot?.decision_set;
  if (!ds || !Array.isArray(ds.decisions)) {
    return [];
  }
  return ds.decisions
    .filter((d) => (d.status ?? "open") === "open" && (d.branches?.length ?? 0) > 1)
    .sort((a, b) => (b.consequence ?? 0) - (a.consequence ?? 0));
}

export function pct(weight: number | undefined): number {
  return Math.round((weight ?? 0) * 100);
}

/** Inline chip for one fate, e.g. "skip 60%". */
export function fateChip(b: DecisionBranchJson): string {
  return `${b.fate_label} ${pct(b.weight)}%`;
}

/** The axis label shown to the user, falling back to the germ. */
export function axisLabel(d: DecisionJson): string {
  return d.axis_label || d.germ || "decision";
}

function reprOf(v: unknown): string {
  if (v === undefined || v === null) {
    return "";
  }
  return typeof v === "string" ? v : JSON.stringify(v);
}

function trunc(s: string, max: number): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length <= max ? t : t.slice(0, max - 1) + "…";
}

/** "input -> output" for a branch, truncated; empty when neither is present. */
export function shortIO(b: DecisionBranchJson, max = 56): string {
  const inS = trunc(reprOf(b.example_in), max);
  const outS = trunc(reprOf(b.example_out), max);
  if (!inS && !outS) {
    return "";
  }
  return `${inS} -> ${outS}`;
}
