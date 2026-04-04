import type { Range, TextEditor, TextEditorDecorationType } from "vscode";
import { Position, Range as VsRange, window } from "vscode";

export type SpecCommentSyntaxTypes = {
  specMarker: TextEditorDecorationType;
  specBody: TextEditorDecorationType;
  reasoningMarker: TextEditorDecorationType;
  reasoningBody: TextEditorDecorationType;
};

/**
 * Fallback "syntax" for #> / #< lines: TextMate injection often does not win against
 * Python/Pylance comment tokenization, so we paint marker + body ranges explicitly.
 * Phrase-level decorations (pattern learning) are applied on top in phraseDecorations.ts.
 */
export function createSpecCommentSyntaxTypes(): SpecCommentSyntaxTypes {
  return {
    specMarker: window.createTextEditorDecorationType({
      fontWeight: "600",
      light: { color: "#008f84" },
      dark: { color: "#4ec9b0" },
    }),
    specBody: window.createTextEditorDecorationType({
      light: { color: "#007a8a" },
      dark: { color: "#9cdcfe" },
    }),
    reasoningMarker: window.createTextEditorDecorationType({
      fontWeight: "600",
      light: { color: "#5a8a3d" },
      dark: { color: "#6a9955" },
    }),
    reasoningBody: window.createTextEditorDecorationType({
      light: { color: "#3d6b2e" },
      dark: { color: "#b5cea8" },
    }),
  };
}

function rangesOnLine(line: string, lineIdx: number): {
  spec: Array<{ marker: Range; body: Range }>;
  reasoning: Array<{ marker: Range; body: Range }>;
} {
  const spec: Array<{ marker: Range; body: Range }> = [];
  const reasoning: Array<{ marker: Range; body: Range }> = [];

  let pos = 0;
  while (pos < line.length) {
    const gt = line.indexOf("#", pos);
    if (gt < 0) {
      break;
    }
    const slice = line.slice(gt);
    const mGt = slice.match(/^#\s*>/);
    const mLt = slice.match(/^#\s*</);
    if (mGt) {
      const markerStart = gt;
      const markerEnd = gt + mGt[0].length;
      const bodyEnd = line.length;
      spec.push({
        marker: new VsRange(new Position(lineIdx, markerStart), new Position(lineIdx, markerEnd)),
        body: new VsRange(new Position(lineIdx, markerEnd), new Position(lineIdx, bodyEnd)),
      });
      pos = markerEnd;
      continue;
    }
    if (mLt) {
      const markerStart = gt;
      const markerEnd = gt + mLt[0].length;
      const bodyEnd = line.length;
      reasoning.push({
        marker: new VsRange(new Position(lineIdx, markerStart), new Position(lineIdx, markerEnd)),
        body: new VsRange(new Position(lineIdx, markerEnd), new Position(lineIdx, bodyEnd)),
      });
      pos = markerEnd;
      continue;
    }
    pos = gt + 1;
  }

  return { spec, reasoning };
}

export function refreshSpecCommentSyntaxDecorations(
  editor: TextEditor,
  types: SpecCommentSyntaxTypes,
): void {
  if (editor.document.languageId !== "python") {
    editor.setDecorations(types.specMarker, []);
    editor.setDecorations(types.specBody, []);
    editor.setDecorations(types.reasoningMarker, []);
    editor.setDecorations(types.reasoningBody, []);
    return;
  }

  const specM: Range[] = [];
  const specB: Range[] = [];
  const reasM: Range[] = [];
  const reasB: Range[] = [];

  const n = editor.document.lineCount;
  for (let lineIdx = 0; lineIdx < n; lineIdx++) {
    const line = editor.document.lineAt(lineIdx).text;
    const { spec, reasoning } = rangesOnLine(line, lineIdx);
    for (const s of spec) {
      specM.push(s.marker);
      specB.push(s.body);
    }
    for (const r of reasoning) {
      reasM.push(r.marker);
      reasB.push(r.body);
    }
  }

  editor.setDecorations(types.specMarker, specM);
  editor.setDecorations(types.specBody, specB);
  editor.setDecorations(types.reasoningMarker, reasM);
  editor.setDecorations(types.reasoningBody, reasB);
}

export function disposeSpecCommentSyntaxTypes(types: SpecCommentSyntaxTypes): void {
  types.specMarker.dispose();
  types.specBody.dispose();
  types.reasoningMarker.dispose();
  types.reasoningBody.dispose();
}
