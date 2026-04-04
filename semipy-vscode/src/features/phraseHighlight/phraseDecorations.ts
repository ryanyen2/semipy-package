import type { Range, TextEditor, TextEditorDecorationType } from "vscode";
import {
  DecorationRangeBehavior,
  Position,
  Range as VsRange,
  window,
} from "vscode";
import type { PortalJson, SpecPhraseJson } from "../../data/types";
import { bindingById, loadSketchLibrary } from "../../data/sketchLoader";
import { hashArrowPrefixRange } from "../../util/hashArrowDetect";
import { activeCommitFromPortalSlot } from "../splitEditor/portalCommit";

const ROLE_ORDER = ["operation", "param", "operator", "connective"];

export function createPhraseDecorationTypes(): Record<string, TextEditorDecorationType> {
  const out: Record<string, TextEditorDecorationType> = {};
  for (const role of ROLE_ORDER) {
    out[role] = window.createTextEditorDecorationType({
      rangeBehavior: DecorationRangeBehavior.ClosedClosed,
      fontWeight: role === "operation" ? "bold" : undefined,
    });
  }
  return out;
}

function sortPhrasesLongestFirst(phrases: SpecPhraseJson[]): SpecPhraseJson[] {
  return [...phrases].sort((a, b) => (b.text || "").length - (a.text || "").length);
}

function phraseSpansInSuffix(
  suffix: string,
  phrases: SpecPhraseJson[],
): Array<{ start: number; end: number; role: string }> {
  const sorted = sortPhrasesLongestFirst(phrases);
  const used: Array<{ start: number; end: number }> = [];
  const spans: Array<{ start: number; end: number; role: string }> = [];

  const overlaps = (s: number, e: number) =>
    used.some((u) => !(e <= u.start || s >= u.end));

  for (const p of sorted) {
    const t = (p.text || "").trim();
    if (!t) {
      continue;
    }
    let search = 0;
    while (search < suffix.length) {
      const pos = suffix.indexOf(t, search);
      if (pos < 0) {
        break;
      }
      const end = pos + t.length;
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

export function refreshPhraseDecorations(
  editor: TextEditor,
  portal: PortalJson | undefined,
  semiformalRoot: string | undefined,
  types: Record<string, TextEditorDecorationType>,
): void {
  for (const t of Object.values(types)) {
    editor.setDecorations(t, []);
  }
  if (!portal || !semiformalRoot) {
    return;
  }
  const lib = loadSketchLibrary(semiformalRoot);
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
    const span = slot.slot_spec?.source_span;
    if (!span || span.length < 3) {
      continue;
    }
    const [, start1, end1] = span;
    for (let lineIdx = start1 - 1; lineIdx <= end1 - 1; lineIdx++) {
      if (lineIdx < 0 || lineIdx >= lines.length) {
        continue;
      }
      const line = lines[lineIdx]!;
      const pref = hashArrowPrefixRange(line);
      if (!pref) {
        continue;
      }
      const suffix = line.slice(pref.end);
      const spans = phraseSpansInSuffix(suffix, binding.phrases);
      const lineObj = doc.lineAt(lineIdx);
      for (const sp of spans) {
        const role = ROLE_ORDER.includes(sp.role) ? sp.role : "param";
        const startCol = pref.end + sp.start;
        const endCol = pref.end + sp.end;
        const r = new VsRange(
          new Position(lineIdx, startCol),
          new Position(lineIdx, endCol),
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
