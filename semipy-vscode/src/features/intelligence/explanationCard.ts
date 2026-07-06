/**
 * The Explanation Card: a trusted-Markdown hover that answers, in one glance,
 * WHY the slot changed, WHAT it now guarantees, and WHAT it touched. Depth lives
 * here so the inline CodeLens can stay a single quiet line.
 */
import type { SlotJson } from "../../data/types";
import type { SlotInsight } from "./slotInsight";

export function relativeTime(tsSeconds: number): string {
  if (!tsSeconds) {
    return "";
  }
  const deltaMs = Date.now() - tsSeconds * 1000;
  const s = Math.round(deltaMs / 1000);
  if (s < 0) {
    return "just now";
  }
  if (s < 60) {
    return `${s}s ago`;
  }
  const m = Math.round(s / 60);
  if (m < 60) {
    return `${m}m ago`;
  }
  const h = Math.round(m / 60);
  if (h < 24) {
    return `${h}h ago`;
  }
  const d = Math.round(h / 24);
  if (d < 30) {
    return `${d}d ago`;
  }
  return new Date(tsSeconds * 1000).toLocaleDateString();
}

function truncate(s: string, n: number): string {
  const t = (s || "").replace(/\s+/g, " ").trim();
  return t.length <= n ? t : t.slice(0, n - 1) + "…";
}

function cmdLink(label: string, command: string, args: unknown[]): string {
  const q = encodeURIComponent(JSON.stringify(args));
  return `[${label}](command:${command}?${q})`;
}

/** Normalize a stored type to a clean name: `<class 'str'>` -> `str`, `builtins.int` -> `int`. */
function cleanTypeName(t: string): string {
  const m = t.match(/<class '([^']+)'>/);
  let name = (m ? m[1] : t).trim();
  name = name.replace(/^builtins\./, "");
  return name;
}

// Control contexts that are unremarkable (the default) -- not worth surfacing.
const TRIVIAL_CONTEXTS = new Set(["", "none", "top_level", "module", "function_body"]);

/** A one-line "what it's checking against" from the slot spec. */
function constraintLine(slot: SlotJson): string {
  const s = slot.slot_spec;
  if (!s) {
    return "";
  }
  const bits: string[] = [];
  if (s.expected_type) {
    bits.push(`returns \`${truncate(cleanTypeName(String(s.expected_type)), 40)}\``);
  }
  const outs = s.output_names;
  if (Array.isArray(outs) && outs.length) {
    bits.push(`output \`${outs.join(", ")}\``);
  }
  const ctx = (s as { control_context?: string }).control_context;
  if (ctx && !TRIVIAL_CONTEXTS.has(ctx)) {
    bits.push(`inside a \`${ctx}\``);
  }
  return bits.join(" · ");
}

/**
 * Build the hover Markdown. The returned string is meant for a MarkdownString
 * created with `supportThemeIcons: true` and `isTrusted: true`.
 */
export function buildHoverMarkdown(slot: SlotJson, insight: SlotInsight): string {
  const lines: string[] = [];
  const lockBadge = insight.locked ? " · $(lock) locked" : "";
  const t = relativeTime(insight.timestamp);
  lines.push(
    `**${insight.glyph} ${insight.decision}** · \`${insight.commitShort}\`${t ? ` · ${t}` : ""}${lockBadge}`,
  );
  lines.push("");

  // WHY -- the rationale for the most recent change.
  if (insight.change?.reason) {
    lines.push(`$(info) **Why** — ${truncate(insight.change.reason, 180)}`);
    lines.push("");
  }

  // EFFECT OF CHANGE -- what regenerating actually changed, and was anything broken.
  if (insight.change && (insight.change.compared > 0 || insight.change.diffs.length > 0)) {
    const ch = insight.change;
    const reg = ch.hasRegression
      ? `$(warning) **${ch.unintended} unintended**`
      : "0 unintended";
    lines.push(`$(diff) **Effect** — +${ch.intended} changed · ${reg}  *(over ${ch.compared} input pattern${ch.compared === 1 ? "" : "s"})*`);
    for (const d of ch.diffs.slice(0, 2)) {
      const mark = d.intended ? "" : " $(warning)";
      lines.push(`> \`${truncate(d.oldRepr, 36)}\` → \`${truncate(d.newRepr, 36)}\`${mark}`);
    }
    lines.push("");
  }

  // GUARANTEES -- the contract, collapsed to distinct assertions (not per-pattern noise).
  const guarantees = insight.contract.guarantees.filter((g) => g.patterns > 0);
  if (guarantees.length) {
    lines.push(`$(law) **Guarantees**`);
    for (const g of guarantees.slice(0, 8)) {
      const span = g.patterns > 1 ? ` *(across ${g.patterns} input patterns)*` : "";
      const detail = g.reason ? ` — ${truncate(g.reason, 70)}` : ` — ${g.meaning}`;
      lines.push(`- \`${g.label}\`${detail}${span}`);
    }
    const quarGroups = insight.contract.guarantees.filter((g) => g.patterns === 0 && g.quarantined > 0);
    const sup = insight.contract.superseded;
    if (quarGroups.length || sup) {
      const bits: string[] = [];
      if (quarGroups.length) bits.push(`${quarGroups.length} quarantined`);
      if (sup) bits.push(`${sup} superseded`);
      lines.push(`- *${bits.join(" · ")}*`);
    }
    lines.push("");
  }

  // FREEZE CERTIFICATE -- the license (or refusal) behind a certified promotion.
  if (insight.freeze) {
    const f = insight.freeze;
    const verdict = f.latestLicensed
      ? "$(check) licensed"
      : "$(circle-slash) refused";
    lines.push(
      `$(shield) **Freeze certificate** — ${verdict} · budget ${f.budgetSpent}/${f.budgetTotal} ` +
        `· ε=${f.epsilon} δ=${f.delta} · held-out ${(f.heldOutPassFraction * 100).toFixed(0)}% · MDL ${f.mdlGain > 0 ? "+" : ""}${f.mdlGain.toFixed(0)}`,
    );
    if (!f.latestLicensed && f.refusalReasons.length) {
      lines.push(`> ${truncate(f.refusalReasons.join("; "), 160)}`);
    }
    if (f.attempts > 1) {
      lines.push(`> ${f.attempts} attempts total · ${f.licensedCount} licensed`);
    }
    lines.push("");
  }

  // WHAT IT CHECKS AGAINST -- the formal constraint the generator/validator enforce.
  const constraint = constraintLine(slot);
  if (constraint) {
    lines.push(`$(symbol-type) **Spec** — ${constraint}`);
    lines.push("");
  }

  // TOUCHES -- real-world effects.
  if (insight.effect.isEffectful) {
    const e = insight.effect;
    const ops = e.latestOps.length ? ` (${e.latestOps.join(", ")})` : "";
    const rev = e.reversible ? "reversible" : "$(warning) irreversible";
    const counts = `applied ${e.applied}×${e.reverted ? ` · reverted ${e.reverted}×` : ""}`;
    lines.push(`$(zap) **Touches** — ${e.targets.join(", ") || "—"}${ops}`);
    lines.push(`> ${rev} · ${counts}${e.pending ? ` · $(warning) ${e.pending} pending approval` : ""}`);
    lines.push("");
  }

  // ACTIONS
  const actions: string[] = [
    cmdLink("$(search) Inspect", "semipy.inspectSlot", [slot.slot_id]),
    cmdLink("$(code) View code", "semipy.viewActiveCode", [slot.slot_id]),
  ];
  const ncommits = Object.keys(slot.commits || {}).length;
  if (ncommits > 1) {
    actions.push(cmdLink("$(history) Switch version", "semipy.pickSlotVersion", [slot.slot_id]));
  }
  if (insight.effect.applied > 0 && insight.effect.latestEventId) {
    actions.push(
      cmdLink("$(discard) Revert effect", "semipy.revertEffect", [slot.slot_id, insight.effect.latestEventId]),
    );
  }
  lines.push(actions.join("  ·  "));

  return lines.join("\n");
}
