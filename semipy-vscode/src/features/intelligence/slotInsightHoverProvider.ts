import type { CancellationToken, Hover, HoverProvider, Position, TextDocument } from "vscode";
import { Hover as VsHover, MarkdownString } from "vscode";
import type { PortalJson, SlotJson } from "../../data/types";
import { pathsEqualRobust } from "../../data/portalLoader";
import { resolveSourceBlockRange } from "../splitEditor/correspondenceMap";
import { resolveSlotUiLines } from "../slotAnnotations/slotLineResolve";
import { buildHoverMarkdown } from "./explanationCard";
import { computeSlotInsight } from "./slotInsight";

/**
 * Rich "Explanation Card" hover anchored on a slot's def/@semiformal line and its
 * `#>` spec block. Shows why it changed, what it guarantees, what it touches.
 */
export function createSlotInsightHoverProvider(
  getPortal: () => PortalJson | undefined,
  enabled: () => boolean,
): HoverProvider {
  return {
    provideHover(document: TextDocument, position: Position, _token: CancellationToken): Hover | undefined {
      if (!enabled() || document.languageId !== "python") {
        return undefined;
      }
      const portal = getPortal();
      if (!portal) {
        return undefined;
      }
      const fsPath = document.uri.fsPath;
      const fullText = document.getText();
      const line0 = position.line;

      for (const slot of Object.values(portal.slots) as SlotJson[]) {
        if (!slot.commits || Object.keys(slot.commits).length === 0) {
          continue;
        }
        const src = slot.slot_spec?.source_span;
        if (!Array.isArray(src) || src.length < 3 || !pathsEqualRobust(src[0] as string, fsPath)) {
          continue;
        }
        const ui = resolveSlotUiLines(document, slot);
        const block = resolveSourceBlockRange(fullText, slot);
        const anchor0 = ui?.codeLensLine0;
        const inBlock = block && line0 >= block.startLine1 - 1 && line0 <= block.endLine1 - 1;
        const onAnchor = anchor0 !== undefined && line0 === anchor0;
        if (!inBlock && !onAnchor) {
          continue;
        }
        const insight = computeSlotInsight(slot);
        if (!insight) {
          continue;
        }
        const md = new MarkdownString(buildHoverMarkdown(slot, insight));
        md.isTrusted = true;
        md.supportThemeIcons = true;
        return new VsHover(md);
      }
      return undefined;
    },
  };
}
