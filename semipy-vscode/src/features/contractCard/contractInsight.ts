/**
 * Contract card insight: turn a portal Slot's contract surface (semipy's U1
 * `ContractSurface` -- cases, regime guards, freeze certificate, scope) into
 * the small set of facts the CodeLens/hover need. Pure + unit-testable; no
 * `vscode` import. Mirrors the shape of `decisionInsight.ts`.
 *
 * KTD-3: this is additive to the `#<` skeleton line, never a duplicate of it.
 * `#<` carries spec-language provenance/effect (intent/given/by/unless/
 * yields/verified/...); this card carries what `#<` cannot say -- hardness,
 * case count, scope status, regime count, and the certified/uncertified
 * boundary -- because both are renders of the same portal-side surface and
 * must never say different things about it.
 */
import type { FreezeCertificateJson, KernelNodeJson, SlotJson } from "../../data/types";
import { activeCommitFromPortalSlot } from "../splitEditor/portalCommit";

/** Typographic glyph for the contract card (no emoji, matches DECISION_GLYPH). */
export const CONTRACT_GLYPH = "§";

function walkNodes(node: KernelNodeJson | undefined): KernelNodeJson[] {
  if (!node) {
    return [];
  }
  const out: KernelNodeJson[] = [node];
  for (const child of node.children ?? []) {
    out.push(...walkNodes(child));
  }
  return out;
}

/** Count of regime guards across the slot's hardness tree; 0 for a single-regime slot. */
export function regimeCount(slot: SlotJson): number {
  let count = 0;
  for (const n of walkNodes(slot.kernel_tree)) {
    count += (n.guards ?? []).length;
  }
  return count;
}

/** The slot's current hardness (molten / plastic / frozen); "plastic" when no
 * tree was computed yet -- the same zero-migration default `kernel.tree.get_tree`
 * assumes for a degenerate (un-lowered) slot. */
export function hardnessChip(slot: SlotJson): string {
  return slot.kernel_tree?.hardness || "plastic";
}

export function caseCounts(slot: SlotJson): { active: number; superseded: number; quarantined: number } {
  const cases = Object.values(slot.contract?.cases ?? {});
  const counts = { active: 0, superseded: 0, quarantined: 0 };
  for (const c of cases) {
    const status = c.status ?? "active";
    if (status === "active") counts.active++;
    else if (status === "superseded") counts.superseded++;
    else if (status === "quarantined") counts.quarantined++;
  }
  return counts;
}

/** The most recent freeze attempt's certificate, or undefined if the slot never tried. */
export function latestCertificate(slot: SlotJson): FreezeCertificateJson | undefined {
  const events = slot.freeze_events ?? [];
  return events.length ? events[events.length - 1]!.certificate : undefined;
}

/** True only when the latest freeze attempt licensed the freeze (the certified/
 * uncertified boundary, D4: an uncertified slot still ships active cases). */
export function isCertified(slot: SlotJson): boolean {
  return !!latestCertificate(slot)?.licensed;
}

/** Whether a scope predicate was minted for the slot's active commit (U2). */
export function hasScopePredicate(slot: SlotJson): boolean {
  const commitId = activeCommitFromPortalSlot(slot)?.commit_id;
  if (!commitId) {
    return false;
  }
  const scopePredicates = slot.advisor_state?.["scope_predicates"] as Record<string, unknown> | undefined;
  return !!scopePredicates?.[commitId];
}

/** Distinct metamorphic relation names asserted by the active cases. */
export function relationsFor(slot: SlotJson): string[] {
  const rels = new Set<string>();
  for (const c of Object.values(slot.contract?.cases ?? {})) {
    if ((c.status ?? "active") === "active" && c.kind === "metamorphic" && c.relation) {
      rels.add(c.relation);
    }
  }
  return [...rels].sort();
}

/** True for a slot with a committed, contentful contract surface -- i.e. not a
 * "plain function" (never generated) and not a generated slot that has yet to
 * accrue any contract state. Mirrors `SemipyCodeLensProvider`'s "skip phantom
 * slots (no commits)" gate, plus requiring something the contract card can
 * actually say. */
export function hasContractSurface(slot: SlotJson): boolean {
  if (!slot.commits || Object.keys(slot.commits).length === 0) {
    return false;
  }
  const counts = caseCounts(slot);
  const hasCases = counts.active + counts.superseded + counts.quarantined > 0;
  const hasCertificate = (slot.freeze_events ?? []).length > 0;
  const hasRegimes = regimeCount(slot) > 0;
  return hasCases || hasCertificate || hasRegimes;
}

/** The compact CodeLens line: hardness chip, case count, scope status, regime count. */
export function codeLensTitle(slot: SlotJson): string {
  const counts = caseCounts(slot);
  const regimes = regimeCount(slot);
  const parts = [
    `${CONTRACT_GLYPH} ${hardnessChip(slot)}`,
    `${counts.active} active case${counts.active === 1 ? "" : "s"}`,
    hasScopePredicate(slot) ? "scope minted" : "no scope minted",
  ];
  if (regimes > 0) {
    parts.push(`${regimes} regime${regimes === 1 ? "" : "s"}`);
  }
  return parts.join("  ·  ");
}

/** The hover card: U1's contract surface, rendered compact, with a dispute action. */
export function contractCardMarkdown(slot: SlotJson): string {
  const lines: string[] = [];
  lines.push(`${CONTRACT_GLYPH} **Contract surface** — \`${hardnessChip(slot)}\``);
  lines.push("");
  const cert = latestCertificate(slot);
  if (isCertified(slot) && cert) {
    lines.push(
      `$(check) **CERTIFIED**: freeze licensed (ε=${cert.epsilon}, δ=${cert.delta}, ` +
        `held-out=${cert.held_out_pass_fraction})`,
    );
  } else {
    lines.push(
      "$(circle-slash) **UNCERTIFIED**: no licensed freeze — partial contract " +
        "(active cases/relations are checkable; whole-slot output not frozen)",
    );
  }
  const counts = caseCounts(slot);
  lines.push(
    `Cases: ${counts.active} active, ${counts.superseded} superseded, ${counts.quarantined} quarantined`,
  );
  lines.push(`Scope: ${hasScopePredicate(slot) ? "minted for the active commit" : "not minted yet"}`);
  const regimes = regimeCount(slot);
  if (regimes > 0) {
    lines.push(`Regimes: ${regimes} guard${regimes === 1 ? "" : "s"}`);
  }
  const relations = relationsFor(slot);
  if (relations.length > 0) {
    lines.push(`Relations: ${relations.join(", ")}`);
  }
  lines.push("");
  const args = encodeURIComponent(JSON.stringify([slot.slot_id]));
  lines.push(`[$(law) Dispute this output…](command:semipy.disputeContract?${args})`);
  return lines.join("\n");
}
