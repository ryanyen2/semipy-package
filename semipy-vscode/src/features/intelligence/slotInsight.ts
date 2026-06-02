/**
 * Slot insight: the single source of truth that turns a portal Slot (+ its active
 * commit) into a compact, human-legible summary of WHAT semipy did, WHY, WHAT it
 * guarantees, and WHAT it touched in the real world.
 *
 * Design principle (mirrors semipy's own `_should_skip_key` minimum-set rule):
 * every chip / glyph is present only when it carries information. A healthy,
 * trivial slot stays as quiet as a plain comment.
 */
import type {
  CommitJson,
  ContractCaseJson,
  ContractCaseKind,
  EffectJson,
  LedgerEventJson,
  SlotContractJson,
  SlotJson,
} from "../../data/types";
import { activeCommitFromPortalSlot } from "../splitEditor/portalCommit";

/** Health drives the one ambient gutter glyph. Priority: danger > warn > effect > ok. */
export type SlotHealth = "ok" | "effect" | "warn" | "danger";

/** One distinct guarantee (assertion), collapsed across the input patterns it holds for. */
export interface Guarantee {
  key: string; // dedupe key
  kind: ContractCaseKind;
  label: string; // e.g. "non_empty", "type_match=str", "whitespace_invariance"
  meaning: string; // plain-language interpretation
  patterns: number; // distinct input patterns it is pinned across (active)
  reason: string; // representative reason (or the meaning if only "initial behavior")
  quarantined: number; // same assertion, quarantined (kept, not enforced)
  caseIds: string[]; // active case ids in this group (for relax/quarantine)
  sampleRepr: string; // short repr of a representative input it is pinned to
}

export interface ContractInsight {
  active: number; // raw active case count (per-pattern)
  superseded: number;
  quarantined: number;
  distinct: number; // distinct guarantees (deduped across patterns) -- the legible count
  guarantees: Guarantee[];
}

/** Plain-language interpretation of each fixed invariant / built-in relation. */
export const GUARANTEE_GLOSSARY: Record<string, string> = {
  non_empty: "output is never empty",
  non_identity: "output actually transforms the input (never echoes it back)",
  type_match: "output is always the declared type",
  category_preserving: "output stays in the same category as the input",
  idempotent: "applying it twice gives the same result as once",
  whitespace_invariance: "leading/trailing whitespace does not change the result",
  case_invariance: "input letter-casing does not change the result",
};

function assertionKey(c: ContractCaseJson): string {
  if (c.kind === "invariant") {
    return `inv:${c.invariant}:${c.expected_type || ""}`;
  }
  if (c.kind === "metamorphic") {
    return `mr:${c.relation}`;
  }
  return `ex:${c.case_id}`; // examples pin distinct outputs -- keep them individual
}

function guaranteeLabel(c: ContractCaseJson): string {
  if (c.kind === "invariant") {
    return `${c.invariant}${c.expected_type ? `=${c.expected_type}` : ""}`;
  }
  if (c.kind === "metamorphic") {
    return c.relation || "relation";
  }
  return "example";
}

function guaranteeMeaning(c: ContractCaseJson): string {
  if (c.kind === "invariant") {
    return GUARANTEE_GLOSSARY[c.invariant || ""] || "a structural property of the output";
  }
  if (c.kind === "metamorphic") {
    return GUARANTEE_GLOSSARY[c.relation || ""] || "a relation between an input and a transformed input";
  }
  return "a pinned input -> output example";
}

const SEEDED_REASONS = new Set(["initial behavior", "adapted for new input pattern"]);

/** Short repr of the value a case is checked against (the first non-self input). */
function primarySampleRepr(c: ContractCaseJson): string {
  const sample = c.input_sample || {};
  for (const [k, v] of Object.entries(sample)) {
    if (k === "self" || k.startsWith("_")) {
      continue;
    }
    const s = typeof v === "string" ? v : JSON.stringify(v);
    const t = (s ?? "").replace(/\s+/g, " ").trim();
    return t.length <= 48 ? t : t.slice(0, 47) + "…";
  }
  return "";
}

/** Collapse active contract cases into distinct guarantees with per-pattern counts. */
export function groupGuarantees(cases: ContractCaseJson[]): Guarantee[] {
  const byKey = new Map<
    string,
    { rep: ContractCaseJson; fps: Set<string>; quarantined: number; caseIds: string[] }
  >();
  for (const c of cases) {
    const status = c.status || "active";
    if (status === "superseded") {
      continue;
    }
    const key = assertionKey(c);
    let g = byKey.get(key);
    if (!g) {
      g = { rep: c, fps: new Set(), quarantined: 0, caseIds: [] };
      byKey.set(key, g);
    }
    if (status === "quarantined") {
      g.quarantined += 1;
    } else {
      g.fps.add(c.input_fingerprint || "");
      if (c.case_id) {
        g.caseIds.push(c.case_id);
      }
      // prefer an active rep with a meaningful (non-seeded) reason
      if (SEEDED_REASONS.has((g.rep.reason || "").trim()) && !SEEDED_REASONS.has((c.reason || "").trim())) {
        g.rep = c;
      }
    }
  }
  const out: Guarantee[] = [];
  for (const [key, g] of byKey) {
    const rep = g.rep;
    const reason = (rep.reason || "").trim();
    out.push({
      key,
      kind: rep.kind,
      label: guaranteeLabel(rep),
      meaning: guaranteeMeaning(rep),
      patterns: g.fps.size,
      reason: SEEDED_REASONS.has(reason) ? "" : reason,
      quarantined: g.quarantined,
      caseIds: g.caseIds,
      sampleRepr: primarySampleRepr(rep),
    });
  }
  // invariants first, then relations, then examples
  const order: Record<string, number> = { invariant: 0, metamorphic: 1, example: 2 };
  return out.sort((a, b) => (order[a.kind] ?? 9) - (order[b.kind] ?? 9));
}

export interface ChangeInsight {
  reason: string;
  decision: string;
  intended: number;
  unintended: number;
  compared: number;
  hasRegression: boolean;
  diffs: Array<{ oldRepr: string; newRepr: string; intended: boolean; inputRepr: string }>;
}

export interface EffectInsight {
  isEffectful: boolean;
  applied: number;
  reverted: number;
  pending: number; // approval_pending / shadow
  targets: string[]; // distinct targets of the latest applied event
  reversible: boolean; // every effect of the latest event carries a compensation
  latestStatus: string;
  latestEventId: string;
  latestOps: string[]; // distinct ops of the latest event
}

export interface SlotInsight {
  decision: string; // GENERATE | ADAPT | REUSE | INSTANTIATE | ...
  glyph: string; // typographic decision glyph
  commitShort: string;
  timestamp: number;
  locked: boolean;
  health: SlotHealth;
  contract: ContractInsight;
  change: ChangeInsight | undefined;
  effect: EffectInsight;
}

const LOCK_REF = "__locked__";

// --- decision vocabulary --------------------------------------------------

/** Typographic glyph per decision. No emoji (kept consistent across all surfaces). */
export function decisionGlyph(decision: string): string {
  switch ((decision || "").toUpperCase()) {
    case "GENERATE":
      return "◆"; // ◆ solid diamond: a fresh build
    case "ADAPT":
      return "◐"; // ◐ half-filled: derived from a parent
    case "REUSE":
      return "↻"; // ↻ reused cached impl
    case "INSTANTIATE":
      return "⧉"; // ⧉ instantiated from a sketch
    case "FORK":
      return "⎇"; // ⎇ branch
    case "MERGE":
      return "⨇";
    default:
      return "◇"; // ◇ hollow diamond
  }
}

// --- contract -------------------------------------------------------------

function activeCases(contract: SlotContractJson | undefined, status: string): ContractCaseJson[] {
  const cases = contract?.cases;
  if (!cases) {
    return [];
  }
  return Object.values(cases).filter((c) => (c.status || "active") === status);
}

function contractInsight(slot: SlotJson): ContractInsight {
  const contract = slot.contract;
  const all = contract?.cases ? Object.values(contract.cases) : [];
  const guarantees = groupGuarantees(all);
  return {
    active: activeCases(contract, "active").length,
    superseded: activeCases(contract, "superseded").length,
    quarantined: activeCases(contract, "quarantined").length,
    distinct: guarantees.filter((g) => g.patterns > 0).length,
    guarantees,
  };
}

// --- change record --------------------------------------------------------

function changeInsight(commit: CommitJson | undefined): ChangeInsight | undefined {
  const cr = commit?.change_record;
  if (!cr || (!cr.reason && !(cr.effect_diff && cr.effect_diff.length))) {
    return undefined;
  }
  const diffs = (cr.effect_diff || []).map((d) => ({
    oldRepr: String(d.old_repr ?? ""),
    newRepr: String(d.new_repr ?? ""),
    intended: !!d.intended,
    inputRepr: String(d.input_repr ?? ""),
  }));
  const unintended = typeof cr.unintended_count === "number"
    ? cr.unintended_count
    : diffs.filter((d) => !d.intended).length;
  return {
    reason: (cr.reason || "").trim(),
    decision: cr.decision || commit?.decision || "",
    intended: diffs.filter((d) => d.intended).length,
    unintended,
    compared: typeof cr.n_compared === "number" ? cr.n_compared : diffs.length,
    hasRegression: unintended > 0,
    diffs,
  };
}

// --- effects ledger -------------------------------------------------------

function distinctTargets(effects: EffectJson[] | undefined): string[] {
  return [...new Set((effects || []).map((e) => e.target).filter(Boolean))];
}

function distinctOps(effects: EffectJson[] | undefined): string[] {
  return [...new Set((effects || []).map((e) => e.op).filter(Boolean))];
}

function effectInsight(slot: SlotJson): EffectInsight {
  const events: LedgerEventJson[] = slot.ledger?.events || [];
  const applied = events.filter((e) => (e.status || "applied") === "applied");
  const reverted = events.filter((e) => e.status === "reverted");
  const pending = events.filter(
    (e) => e.status === "approval_pending" || e.status === "shadow",
  );
  const latest = events.length ? events[events.length - 1] : undefined;
  const latestEffects = latest?.applied_effects || [];
  const mutating = latestEffects.filter((e) => e.op !== "read" && e.op !== "call");
  const reversible = mutating.length > 0 && mutating.every((e) => !!e.compensation);
  return {
    isEffectful: events.length > 0,
    applied: applied.length,
    reverted: reverted.length,
    pending: pending.length,
    targets: distinctTargets(latestEffects),
    reversible,
    latestStatus: latest?.status || "",
    latestEventId: latest?.event_id || "",
    latestOps: distinctOps(latestEffects),
  };
}

// --- top-level ------------------------------------------------------------

export function computeSlotInsight(slot: SlotJson): SlotInsight | undefined {
  if (!slot.commits || Object.keys(slot.commits).length === 0) {
    return undefined;
  }
  const commit = activeCommitFromPortalSlot(slot);
  const contract = contractInsight(slot);
  const change = changeInsight(commit);
  const effect = effectInsight(slot);
  const locked = !!slot.refs?.[LOCK_REF];

  let health: SlotHealth = "ok";
  if (change?.hasRegression) {
    health = "danger";
  } else if (contract.quarantined > 0 || effect.pending > 0) {
    health = "warn";
  } else if (effect.isEffectful) {
    health = "effect";
  }

  return {
    decision: (commit?.decision || "?").toUpperCase(),
    glyph: decisionGlyph(commit?.decision || ""),
    commitShort: commit?.commit_id?.slice(0, 8) ?? "?",
    timestamp: commit?.timestamp ?? 0,
    locked,
    health,
    contract,
    change,
    effect,
  };
}

// --- the one-line health sentence (CodeLens / inlay) ----------------------

/** Compact chips for the CodeLens title. Restraint rule: each chip only if informative. */
export function insightChips(insight: SlotInsight): string[] {
  const chips: string[] = [`${insight.glyph} ${insight.decision}`];
  if (insight.locked) {
    chips.push("locked");
  }
  const c = insight.contract;
  if (c.distinct > 0) {
    chips.push(`✓${c.distinct} hold`); // distinct guarantees, not per-pattern noise
  }
  if (c.quarantined > 0) {
    chips.push(`⚠${c.quarantined} quarantined`); // ⚠
  }
  if (insight.change?.hasRegression) {
    chips.push(`⚠${insight.change.unintended} regression${insight.change.unintended === 1 ? "" : "s"}`);
  }
  const e = insight.effect;
  if (e.isEffectful) {
    const tgt = e.targets[0] || "effect";
    const more = e.targets.length > 1 ? ` +${e.targets.length - 1}` : "";
    let state = e.latestStatus || "applied";
    if (state === "approval_pending") {
      state = "approval pending";
    }
    chips.push(`⚡ ${tgt}${more} ${state}`); // ⚡
  }
  return chips;
}
