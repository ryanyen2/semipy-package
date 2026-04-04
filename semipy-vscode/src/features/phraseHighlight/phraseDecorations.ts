import type { Range, TextEditor, TextEditorDecorationType } from "vscode";
import {
  DecorationRangeBehavior,
  Position,
  Range as VsRange,
  window,
} from "vscode";
import type { PortalJson, SpecPhraseJson } from "../../data/types";
import { bindingById, loadSketchLibraryMerged } from "../../data/sketchLoader";
import { hashArrowSpecSuffixFromLine } from "../../util/hashArrowDetect";
import { activeCommitFromPortalSlot } from "../splitEditor/portalCommit";
import { resolveSourceBlockRange } from "../splitEditor/correspondenceMap";

const ROLE_ORDER = ["operation", "param", "operator", "connective"];

/** Visible role colors (semantic binding / pattern-learning phrases); works on light and dark themes. */
const ROLE_STYLES: Record<
  string,
  { light: { color: string; backgroundColor: string }; dark: { color: string; backgroundColor: string } }
> = {
  operation: {
    light: { color: "#00639c", backgroundColor: "rgba(0, 99, 156, 0.14)" },
    dark: { color: "#4ec9b0", backgroundColor: "rgba(78, 201, 176, 0.18)" },
  },
  param: {
    light: { color: "#a31515", backgroundColor: "rgba(163, 21, 21, 0.1)" },
    dark: { color: "#ce9178", backgroundColor: "rgba(206, 145, 120, 0.18)" },
  },
  operator: {
    light: { color: "#811f3f", backgroundColor: "rgba(129, 31, 63, 0.1)" },
    dark: { color: "#dcdcaa", backgroundColor: "rgba(220, 220, 170, 0.14)" },
  },
  connective: {
    light: { color: "#444444", backgroundColor: "rgba(68, 68, 68, 0.08)" },
    dark: { color: "#9cdcfe", backgroundColor: "rgba(156, 220, 254, 0.12)" },
  },
};

export function createPhraseDecorationTypes(): Record<string, TextEditorDecorationType> {
  const out: Record<string, TextEditorDecorationType> = {};
  for (const role of ROLE_ORDER) {
    const st = ROLE_STYLES[role] ?? ROLE_STYLES.param;
    out[role] = window.createTextEditorDecorationType({
      rangeBehavior: DecorationRangeBehavior.ClosedClosed,
      light: { ...st.light, fontWeight: role === "operation" ? "600" : undefined },
      dark: { ...st.dark, fontWeight: role === "operation" ? "600" : undefined },
    });
  }
  return out;
}

function sortPhrasesLongestFirst(phrases: SpecPhraseJson[]): SpecPhraseJson[] {
  return [...phrases].sort((a, b) => (b.text || "").length - (a.text || "").length);
}

/** Map NL phrases to columns in the spec suffix; case-independent substring match. */
function phraseSpansInSuffix(
  suffix: string,
  phrases: SpecPhraseJson[],
): Array<{ start: number; end: number; role: string }> {
  const sorted = sortPhrasesLongestFirst(phrases);
  const used: Array<{ start: number; end: number }> = [];
  const spans: Array<{ start: number; end: number; role: string }> = [];

  const overlaps = (s: number, e: number) =>
    used.some((u) => !(e <= u.start || s >= u.end));

  const lowerSuffix = suffix.toLowerCase();

  for (const p of sorted) {
    const t = (p.text || "").trim();
    if (!t) {
      continue;
    }
    const tl = t.toLowerCase();
    let search = 0;
    while (search <= lowerSuffix.length) {
      const pos = lowerSuffix.indexOf(tl, search);
      if (pos < 0) {
        break;
      }
      const end = pos + t.length;
      if (suffix.slice(pos, end).toLowerCase() !== tl) {
        search = pos + 1;
        continue;
      }
      if (!overlaps(pos, end)) {
        used.push({ start: pos, end });
        spans.push({ start: pos, end, role: p.role || "param" });
        break;
      }
      search = pos + 1;
    }
  }
  return spans;
}

/** `portalCacheDir` is semipy `cache_dir` (where `sketch_library.json` and portals live). */
export function refreshPhraseDecorations(
  editor: TextEditor,
  portal: PortalJson | undefined,
  portalCacheDir: string | undefined,
  types: Record<string, TextEditorDecorationType>,
  workspaceRoots: readonly string[] | undefined,
): void {
  for (const t of Object.values(types)) {
    editor.setDecorations(t, []);
  }
  if (!portal || !portalCacheDir) {
    return;
  }
  const lib = loadSketchLibraryMerged(portalCacheDir, workspaceRoots);
  const doc = editor.document;
  const full = doc.getText();
  const lines = full.split(/\r?\n/);

  const rangesByRole: Record<string, Range[]> = {};
  for (const r of ROLE_ORDER) {
    rangesByRole[r] = [];
  }

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
    const block = resolveSourceBlockRange(full, slot);
    if (!block) {
      continue;
    }
    const { startLine1: start1, endLine1: end1 } = block;
    for (let lineIdx = start1 - 1; lineIdx <= end1 - 1; lineIdx++) {
      if (lineIdx < 0 || lineIdx >= lines.length) {
        continue;
      }
      const line = lines[lineIdx]!;
      const specRegion = hashArrowSpecSuffixFromLine(line);
      if (!specRegion) {
        continue;
      }
      const suffix = specRegion.suffix;
      const spans = phraseSpansInSuffix(suffix, binding.phrases);
      const lineObj = doc.lineAt(lineIdx);
      for (const sp of spans) {
        const role = ROLE_ORDER.includes(sp.role) ? sp.role : "param";
        const startCol = specRegion.baseCol + sp.start;
        const endCol = specRegion.baseCol + sp.end;
        const r = new VsRange(
          new Position(lineIdx, startCol),
          new Position(lineIdx, Math.min(endCol, lineObj.text.length)),
        );
        rangesByRole[role]!.push(r);
      }
    }
  }

  for (const role of ROLE_ORDER) {
    const t = types[role];
    if (t) {
      editor.setDecorations(t, rangesByRole[role] || []);
    }
  }
}
