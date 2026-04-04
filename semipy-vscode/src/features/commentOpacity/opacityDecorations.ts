import type { TextEditor, TextEditorDecorationType } from "vscode";
import { OverviewRulerLane, window, workspace } from "vscode";
import { isReasoningLine } from "../../util/hashArrowDetect";

export function createOpacityDecorationTypes(): {
  reasoningDim: TextEditorDecorationType;
} {
  const reasoningDim = window.createTextEditorDecorationType({
    isWholeLine: true,
    opacity: "0.65",
    overviewRulerColor: "rgba(120,120,120,0.35)",
    overviewRulerLane: OverviewRulerLane.Left,
  });
  return { reasoningDim };
}

export function refreshOpacityDecorations(
  editor: TextEditor,
  reasoningDim: TextEditorDecorationType,
): void {
  if (editor.document.languageId !== "python") {
    editor.setDecorations(reasoningDim, []);
    return;
  }
  const text = editor.document.getText();
  const lines = text.split(/\r?\n/);
  const reasoning: import("vscode").Range[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (isReasoningLine(line)) {
      reasoning.push(editor.document.lineAt(i).range);
    }
  }
  editor.setDecorations(reasoningDim, reasoning);
}

export function subscribeOpacity(
  types: ReturnType<typeof createOpacityDecorationTypes>,
  debounceMs: number,
): { dispose: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const tick = (editor: TextEditor | undefined) => {
    if (!editor) {
      return;
    }
    refreshOpacityDecorations(editor, types.reasoningDim);
  };
  const sub1 = window.onDidChangeActiveTextEditor((e) => tick(e));
  const sub2 = workspace.onDidChangeTextDocument((ev) => {
    const ed = window.activeTextEditor;
    if (!ed || ev.document !== ed.document) {
      return;
    }
    if (timer) {
      clearTimeout(timer);
    }
    timer = setTimeout(() => {
      timer = undefined;
      tick(ed);
    }, debounceMs);
  });
  tick(window.activeTextEditor);
  return {
    dispose: () => {
      sub1.dispose();
      sub2.dispose();
      if (timer) {
        clearTimeout(timer);
      }
      types.reasoningDim.dispose();
    },
  };
}
