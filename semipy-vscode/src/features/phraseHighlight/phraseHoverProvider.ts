import type { HoverProvider, Position, TextDocument } from "vscode";
import { Hover } from "vscode";
import { MarkdownString } from "vscode";
import type { PortalJson } from "../../data/types";
import { bindingById, loadSketchLibraryMerged } from "../../data/sketchLoader";
import { hashArrowSpecSuffixFromLine } from "../../util/hashArrowDetect";
import { activeCommitFromPortalSlot } from "../splitEditor/portalCommit";
import { resolveSourceBlockRange } from "../splitEditor/correspondenceMap";

export function createPhraseHoverProvider(
  getPortal: () => PortalJson | undefined,
  getPortalCacheDir: () => string | undefined,
  getWorkspaceRoots: () => readonly string[] | undefined,
): HoverProvider {
  return {
    provideHover(document: TextDocument, pos: Position): Hover | undefined {
      if (document.languageId !== "python") {
        return undefined;
      }
      const portal = getPortal();
      const cacheDir = getPortalCacheDir();
      if (!portal || !cacheDir) {
        return undefined;
      }
      const line1 = pos.line + 1;
      const fullText = document.getText();
      const lineText = document.lineAt(pos.line).text;
      const specRegion = hashArrowSpecSuffixFromLine(lineText);
      if (!specRegion || pos.character < specRegion.baseCol) {
        return undefined;
      }
      const lib = loadSketchLibraryMerged(cacheDir, getWorkspaceRoots());
      const suffix = specRegion.suffix;
      const rel = pos.character - specRegion.baseCol;

      const lowerSuffix = suffix.toLowerCase();

      for (const slot of Object.values(portal.slots)) {
        const head = activeCommitFromPortalSlot(slot);
        const bid = head?.binding_id || "";
        if (!bid) {
          continue;
        }
        const binding = bindingById(lib, bid);
        if (!binding?.phrases?.length) {
          continue;
        }
        const block = resolveSourceBlockRange(fullText, slot);
        if (!block || line1 < block.startLine1 || line1 > block.endLine1) {
          continue;
        }
        const sorted = [...binding.phrases].sort((x, y) => y.text.length - x.text.length);
        for (const p of sorted) {
          const t = (p.text || "").trim();
          if (!t) {
            continue;
          }
          const tl = t.toLowerCase();
          let idx = 0;
          while (idx <= lowerSuffix.length) {
            const at = lowerSuffix.indexOf(tl, idx);
            if (at < 0) {
              break;
            }
            if (suffix.slice(at, at + t.length).toLowerCase() !== tl) {
              idx = at + 1;
              continue;
            }
            if (rel >= at && rel < at + t.length) {
              const md = new MarkdownString();
              md.appendMarkdown(`**${p.role}**\n\n`);
              md.appendMarkdown(`code referent: \`${p.code_referent || ""}\`\n\n`);
              if (p.hole_name) {
                md.appendMarkdown(`hole: \`${p.hole_name}\`\n\n`);
              }
              if (p.safe_swap_set?.length) {
                md.appendMarkdown(`safe swaps: ${p.safe_swap_set.map((s) => `\`${s}\``).join(", ")}`);
              }
              return new Hover(md);
            }
            idx = at + 1;
          }
        }
      }
      return undefined;
    },
  };
}
