/**
 * Decision hover card: the informative, minimal explanation behind the inline
 * picker. Shown on the slot anchor / spec line for a slot with an open fork.
 *
 * It says, in the user's language: the model guessed here, what is being decided,
 * the distribution, the guard, and each fate's concrete input -> output -- then
 * offers the same Pick / Assert actions as command links. Mirrors the shape of
 * `createSteeringHoverProvider` (reasoningSteering.ts).
 */
import type { CancellationToken, Hover, HoverProvider, Position, TextDocument } from "vscode";
import { Hover as VsHover, MarkdownString } from "vscode";
import type { DecisionJson, PortalJson, SlotJson } from "../../data/types";
import { resolveSlotUiLines } from "../slotAnnotations/slotLineResolve";
import { DECISION_GLYPH, axisLabel, openDecisionsFor, pct, shortIO } from "./decisionInsight";

function decisionCard(slotId: string, d: DecisionJson): string {
  const lines: string[] = [];
  lines.push(`${DECISION_GLYPH} **Silent decision** — \`${axisLabel(d)}\``);
  lines.push("");
  lines.push("The model had to guess here. Pick the behavior you meant, or assert a property it must satisfy.");
  if (d.guard) {
    lines.push("");
    lines.push(`*Triggers when:* ${d.guard}`);
  }
  lines.push("");
  for (const b of d.branches) {
    const io = shortIO(b);
    const tail = io ? `  \`${io}\`` : "";
    lines.push(`- **${b.fate_label}** · ${pct(b.weight)}%${tail}`);
  }
  lines.push("");
  const links = d.branches.map((b) => {
    const q = encodeURIComponent(JSON.stringify([slotId, d.decision_id, b.fate_label]));
    return `[$(check) ${b.fate_label}](command:semipy.pickDecision?${q})`;
  });
  const aq = encodeURIComponent(JSON.stringify([slotId, d.decision_id]));
  links.push(`[$(pencil) Assert a property…](command:semipy.assertDecision?${aq})`);
  lines.push(links.join("  ·  "));
  return lines.join("\n");
}

export function createDecisionHoverProvider(
  getPortal: () => PortalJson | undefined,
  enabled: () => boolean,
): HoverProvider {
  return {
    provideHover(document: TextDocument, position: Position, _token: CancellationToken): Hover | undefined {
      if (!enabled()) {
        return undefined;
      }
      const portal = getPortal();
      if (!portal || document.languageId !== "python") {
        return undefined;
      }
      for (const slot of Object.values(portal.slots) as SlotJson[]) {
        const decisions = openDecisionsFor(slot);
        if (decisions.length === 0) {
          continue;
        }
        const ui = resolveSlotUiLines(document, slot);
        if (!ui) {
          continue;
        }
        // Hover anywhere on the slot anchor (def/@semiformal) or its #> spec line.
        if (position.line !== ui.codeLensLine0 && position.line !== ui.inlayLine0) {
          continue;
        }
        const md = new MarkdownString();
        md.isTrusted = true;
        md.supportThemeIcons = true;
        md.appendMarkdown(decisions.map((d) => decisionCard(slot.slot_id, d)).join("\n\n---\n\n"));
        return new VsHover(md);
      }
      return undefined;
    },
  };
}
