import type { HoverProvider, Position, TextDocument } from "vscode";
import { Hover } from "vscode";
import { MarkdownString } from "vscode";
import type { PortalJson } from "../../data/types";
import { bindingById, loadSketchLibrary } from "../../data/sketchLoader";
import { hashArrowPrefixRange } from "../../util/hashArrowDetect";
import { activeCommitFromPortalSlot } from "../splitEditor/portalCommit";

export function createPhraseHoverProvider(
  getPortal: () => PortalJson | undefined,
  getSemiformalRoot: () => string | undefined,
): HoverProvider {
  return {
    provideHover(document: TextDocument, pos: Position): Hover | undefined {
      if (document.languageId !== "python") {
        return undefined;
      }
      const portal = getPortal();
      const root = getSemiformalRoot();
      if (!portal || !root) {
        return undefined;
      }
      const line1 = pos.line + 1;
      const lineText = document.lineAt(pos.line).text;
      const pref = hashArrowPrefixRange(lineText);
      if (!pref || pos.character < pref.end) {
        return undefined;
      }
      const lib = loadSketchLibrary(root);
      const suffix = lineText.slice(pref.end);
      const rel = pos.character - pref.end;

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
        const span = slot.slot_spec?.source_span;
        if (!span || span.length < 3) {
          continue;
        }
        const [, a, b] = span;
        if (line1 < a || line1 > b) {
          continue;
        }
        const sorted = [...binding.phrases].sort((x, y) => y.text.length - x.text.length);
        for (const p of sorted) {
          const t = (p.text || "").trim();
          if (!t) {
            continue;
          }
          let idx = 0;
          while (idx < suffix.length) {
            const at = suffix.indexOf(t, idx);
            if (at < 0) {
              break;
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
