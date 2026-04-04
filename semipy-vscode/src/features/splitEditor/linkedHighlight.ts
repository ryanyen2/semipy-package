import * as path from "path";
import type { TextEditor, TextEditorDecorationType } from "vscode";
import { ThemeColor, Uri, Range, window } from "vscode";
import type { PortalJson } from "../../data/types";
import {
  dispatchRangeForSlot,
  findSlotForSourceLine,
  pathsEqual,
  resolveSourceBlockRange,
} from "./correspondenceMap";

export class LinkedHighlightCoordinator {
  private highlight: TextEditorDecorationType;
  private fadeTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(private readonly fadeMs: () => number) {
    this.highlight = window.createTextEditorDecorationType({
      backgroundColor: new ThemeColor("editor.wordHighlightBackground"),
      isWholeLine: false,
    });
  }

  dispose(): void {
    if (this.fadeTimer) {
      clearTimeout(this.fadeTimer);
    }
    this.highlight.dispose();
  }

  onSelectionOrPortal(
    editor: TextEditor | undefined,
    portal: PortalJson | undefined,
    portalCacheDir: string | undefined,
  ): void {
    if (this.fadeTimer) {
      clearTimeout(this.fadeTimer);
      this.fadeTimer = undefined;
    }
    for (const ed of window.visibleTextEditors) {
      ed.setDecorations(this.highlight, []);
    }
    if (!editor || !portal || !portalCacheDir) {
      return;
    }
    const doc = editor.document;
    const docPath = doc.uri.fsPath;
    const sel = editor.selection.active;
    const line1 = sel.line + 1;
    const fullText = doc.getText();

    const dispatchPath = path.join(portalCacheDir, "runtime", `${portal.module_name}.semi.py`);

    if (pathsEqual(docPath, dispatchPath) || doc.uri.fsPath.endsWith(".semi.py")) {
      this.highlightDispatchToSource(editor, portal, portalCacheDir);
      return;
    }

    const slot = findSlotForSourceLine(portal, docPath, line1, fullText);
    if (!slot) {
      return;
    }
    const dr = dispatchRangeForSlot(portal, slot.slot_id, portalCacheDir);
    if (!dr) {
      return;
    }
    const targetUri = Uri.file(dr.uriPath);
    const dispEd = window.visibleTextEditors.find(
      (e) => e.document.uri.toString() === targetUri.toString(),
    );
    if (!dispEd) {
      return;
    }
    const start = Math.max(1, dr.startLine1) - 1;
    const end = Math.max(1, dr.endLine1) - 1;
    const ranges: Range[] = [];
    for (let i = start; i <= end; i++) {
      if (i < dispEd.document.lineCount) {
        ranges.push(dispEd.document.lineAt(i).range);
      }
    }
    dispEd.setDecorations(this.highlight, ranges);
    this.scheduleFade();
  }

  private highlightDispatchToSource(editor: TextEditor, portal: PortalJson, portalCacheDir: string): void {
    const line1 = editor.selection.active.line + 1;
    for (const slot of Object.values(portal.slots)) {
      const dr = dispatchRangeForSlot(portal, slot.slot_id, portalCacheDir);
      if (!dr) {
        continue;
      }
      if (!pathsEqual(dr.uriPath, editor.document.uri.fsPath)) {
        continue;
      }
      if (line1 >= dr.startLine1 && line1 <= dr.endLine1) {
        const sp = slot.slot_spec?.source_span;
        if (!sp || sp.length < 3) {
          return;
        }
        const [srcFile] = sp;
        const srcUri = Uri.file(srcFile);
        const srcEd = window.visibleTextEditors.find(
          (e) => e.document.uri.toString() === srcUri.toString(),
        );
        if (!srcEd) {
          return;
        }
        const full = srcEd.document.getText();
        const block = resolveSourceBlockRange(full, slot);
        const ranges: Range[] = [];
        const a = block?.startLine1 ?? (sp[1] as number);
        const b = block?.endLine1 ?? (sp[2] as number);
        for (let i = a - 1; i <= b - 1; i++) {
          if (i < srcEd.document.lineCount) {
            ranges.push(srcEd.document.lineAt(i).range);
          }
        }
        srcEd.setDecorations(this.highlight, ranges);
        this.scheduleFade();
        return;
      }
    }
  }

  private scheduleFade(): void {
    const ms = this.fadeMs();
    this.fadeTimer = setTimeout(() => {
      this.fadeTimer = undefined;
      for (const ed of window.visibleTextEditors) {
        ed.setDecorations(this.highlight, []);
      }
    }, ms);
  }
}
