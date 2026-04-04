import * as Diff from "diff";
import type { TextDocument, TextDocumentChangeEvent } from "vscode";
import {
  TextDocumentChangeReason,
  WorkspaceEdit,
  workspace,
} from "vscode";
import { isReasoningLine } from "../../util/hashArrowDetect";

function splitLines(text: string): string[] {
  const t = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (t === "") {
    return [];
  }
  return t.split("\n");
}

/** Heuristic: tool/skeleton edits are usually multi-line, large, or many chunks. */
export function shouldSkipSignFlipForBulkEdit(
  e: TextDocumentChangeEvent,
  before: string,
  after: string,
): boolean {
  const oldL = before.split(/\r?\n/).length;
  const newL = after.split(/\r?\n/).length;
  if (Math.abs(newL - oldL) > 4) {
    return true;
  }
  let total = 0;
  let maxNl = 0;
  for (const c of e.contentChanges) {
    total += c.text.length;
    maxNl = Math.max(maxNl, (c.text.match(/\n/g) || []).length);
  }
  if (total > 500) {
    return true;
  }
  if (maxNl >= 4) {
    return true;
  }
  if (e.contentChanges.length > 12) {
    return true;
  }
  return false;
}

/**
 * Replace leading `#<` / `# <` with `#>` (spec ownership) preserving indentation.
 */
export function rewriteReasoningPrefixToSpec(line: string): string | null {
  const m = line.match(/^(\s*)(#\s*<)/);
  if (!m) {
    return null;
  }
  const leadLen = m[1]!.length;
  const prefixLen = m[2]!.length;
  return line.slice(0, leadLen) + "#>" + line.slice(leadLen + prefixLen);
}

/**
 * When a #< line's text changes while staying a #< line, rewrite the prefix to #>.
 * Skips Undo/Redo. Suppresses re-entrancy when applying the edit.
 */
export class SignFlipCoordinator {
  private previousText = new Map<string, string>();
  private applying = new Set<string>();

  constructor(
    private readonly enabled: () => boolean,
    private readonly skipApiEdits: () => boolean,
  ) {}

  attach(): { dispose: () => void } {
    const sub = workspace.onDidChangeTextDocument((e) => this.onChange(e));
    return {
      dispose: () => sub.dispose(),
    };
  }

  private onChange(e: TextDocumentChangeEvent): void {
    if (e.document.languageId !== "python") {
      return;
    }
    const uriKey = e.document.uri.toString();
    if (e.reason === TextDocumentChangeReason.Undo || e.reason === TextDocumentChangeReason.Redo) {
      this.previousText.set(uriKey, e.document.getText());
      return;
    }
    const reasonApi = 3 as const;
    if (this.skipApiEdits() && e.reason === reasonApi) {
      this.previousText.set(uriKey, e.document.getText());
      return;
    }
    if (this.applying.has(uriKey)) {
      this.previousText.set(uriKey, e.document.getText());
      return;
    }
    if (!this.enabled()) {
      this.previousText.set(uriKey, e.document.getText());
      return;
    }
    const before = this.previousText.get(uriKey);
    const after = e.document.getText();
    if (before === undefined || before === after) {
      this.previousText.set(uriKey, after);
      return;
    }
    if (shouldSkipSignFlipForBulkEdit(e, before, after)) {
      this.previousText.set(uriKey, after);
      return;
    }
    this.previousText.set(uriKey, after);
    const flips = collectFlipLineNumbers1Based(before, after);
    if (flips.length === 0) {
      return;
    }
    const edit = new WorkspaceEdit();
    for (const line1 of flips) {
      if (line1 < 1 || line1 > e.document.lineCount) {
        continue;
      }
      const line = e.document.lineAt(line1 - 1);
      const fixed = rewriteReasoningPrefixToSpec(line.text);
      if (fixed === null || fixed === line.text) {
        continue;
      }
      edit.replace(e.document.uri, line.range, fixed);
    }
    if (edit.size === 0) {
      return;
    }
    this.applying.add(uriKey);
    void workspace.applyEdit(edit).then(
      (ok) => {
        this.applying.delete(uriKey);
        if (ok) {
          this.previousText.set(uriKey, e.document.getText());
        }
      },
      () => {
        this.applying.delete(uriKey);
      },
    );
  }

  seedDocument(doc: TextDocument): void {
    this.previousText.set(doc.uri.toString(), doc.getText());
  }
}

/** Lines in `after` (1-based) that were #<, still #<, and content changed. */
export function collectFlipLineNumbers1Based(before: string, after: string): number[] {
  const b = splitLines(before);
  const a = splitLines(after);
  if (b.length === a.length) {
    const out: number[] = [];
    for (let i = 0; i < a.length; i++) {
      if (isReasoningLine(b[i]!) && isReasoningLine(a[i]!) && b[i] !== a[i]) {
        out.push(i + 1);
      }
    }
    return out;
  }
  const out: number[] = [];
  const parts = Diff.diffLines(before, after);
  let newLine = 1;
  let i = 0;
  while (i < parts.length) {
    const p = parts[i]!;
    if (!p.added && !p.removed) {
      newLine += splitLines(p.value).length;
      i += 1;
      continue;
    }
    if (p.removed && i + 1 < parts.length && parts[i + 1]!.added) {
      const oldL = splitLines(p.value);
      const newL = splitLines(parts[i + 1]!.value);
      const n = Math.min(oldL.length, newL.length);
      for (let j = 0; j < n; j++) {
        if (
          isReasoningLine(oldL[j]!) &&
          isReasoningLine(newL[j]!) &&
          oldL[j] !== newL[j]
        ) {
          out.push(newLine + j);
        }
      }
      newLine += newL.length;
      i += 2;
      continue;
    }
    if (p.added) {
      newLine += splitLines(p.value).length;
      i += 1;
      continue;
    }
    if (p.removed) {
      i += 1;
      continue;
    }
    i += 1;
  }
  return out;
}
