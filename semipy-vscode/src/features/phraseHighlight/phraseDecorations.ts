import type { Range, TextEditor, TextEditorDecorationType } from "vscode";
import {
  DecorationRangeBehavior,
  Position,
  Range as VsRange,
  window,
  workspace,
} from "vscode";
import type { PortalJson, SpecPhraseJson } from "../../data/types";
import {
  bindingById,
  loadSketchLibraryMerged,
  resolveBindingIdForCommit,
} from "../../data/sketchLoader";
import { appendSemipyLog } from "../../logging/semipyOutputChannel";
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
  const trace =
    workspace.getConfiguration("semipy").get<boolean>("tracePhraseDecorations") ?? false;

  if (trace) {
    appendSemipyLog(`[phrase] refresh ${doc.uri.fsPath}`);
    appendSemipyLog(
      `  cacheDir=${portalCacheDir} lib=${lib ? "ok" : "missing"} bindingKeys=${lib?.bindings ? Object.keys(lib.bindings).length : 0}`,
    );
  }

  const rangesByRole: Record<string, Range[]> = {};
  for (const r of ROLE_ORDER) {
    rangesByRole[r] = [];
  }

  for (const slot of Object.values(portal.slots)) {
    const head = activeCommitFromPortalSlot(slot);
    const cid = head?.commit_id || "";
    const bid = resolveBindingIdForCommit(lib, cid, head?.binding_id) || "";
    if (trace) {
      appendSemipyLog(
        `  slot ${slot.slot_id.slice(0, 8)} commit ${cid.slice(0, 12) || "?"} resolved binding_id=${bid || "none"}`,
      );
    }
    if (!bid) {
      continue;
    }
    const binding = bindingById(lib, bid);
    if (!binding?.phrases?.length) {
      if (trace) {
        appendSemipyLog(`    no phrases for binding ${bid.slice(0, 8)}`);
      }
      continue;
    }
    const block = resolveSourceBlockRange(full, slot);
    if (!block) {
      if (trace) {
        appendSemipyLog(`    no source block range`);
      }
      continue;
    }
    const { startLine1: start1, endLine1: end1 } = block;
    let spanTotal = 0;
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
      spanTotal += spans.length;
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
    if (trace) {
      appendSemipyLog(`    block lines ${start1}-${end1} phraseSpans=${spanTotal}`);
    }
  }

  for (const role of ROLE_ORDER) {
    const t = types[role];
    if (t) {
      editor.setDecorations(t, rangesByRole[role] || []);
    }
  }
}
