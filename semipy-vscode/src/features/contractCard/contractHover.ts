/**
 * Contract card hover: U1's contract surface, rendered compact, on the slot
 * anchor / spec line. Complements (does not replace) the Explanation Card
 * (`slotInsightHoverProvider.ts`) and the decision hover (`decisionHover.ts`)
 * -- multiple hover providers already stack their Markdown on this codebase's
 * conventions. Degrades to nothing when the slot has no contract surface yet
 * (`hasContractSurface`), leaving the decision hover as the sole surface for
 * back-compat portals (test scenario 4).
 */
import type { CancellationToken, Hover, HoverProvider, Position, TextDocument } from "vscode";
import { Hover as VsHover, MarkdownString } from "vscode";
import type { PortalJson, SlotJson } from "../../data/types";
import { resolveSlotUiLines } from "../slotAnnotations/slotLineResolve";
import { contractCardMarkdown, hasContractSurface } from "./contractInsight";

export function createContractHoverProvider(
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
        if (!hasContractSurface(slot)) {
          continue;
        }
        const ui = resolveSlotUiLines(document, slot);
        if (!ui) {
          continue;
        }
        if (position.line !== ui.codeLensLine0 && position.line !== ui.inlayLine0) {
          continue;
        }
        const md = new MarkdownString();
        md.isTrusted = true;
        md.supportThemeIcons = true;
        md.appendMarkdown(contractCardMarkdown(slot));
        return new VsHover(md);
      }
      return undefined;
    },
  };
}
