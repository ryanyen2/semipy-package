/**
 * Opacity = authorship, in the generated dispatch file.
 *
 * `<cache>/runtime/<module>.semi.py` is entirely LLM-authored. We dim it so it
 * reads as machine output -- and un-dim (full opacity) any line the *user* has
 * since edited or added. "User-authored" is computed without tracking keystrokes:
 * we diff the buffer against the committed `generated_source` (content set, so it
 * is robust to formatting/line offsets). A line whose content matches generated
 * code (or is a dispatch comment / trivial scaffold) stays dim; a novel line is
 * the user's, and pops to full opacity -- a standing signal that a manual edit
 * here is at risk of being overwritten on the next regenerate.
 */
import * as fs from "fs";
import * as path from "path";
import type { TextEditor, TextEditorDecorationType } from "vscode";
import { Range, window } from "vscode";
import type { PortalJson } from "../../data/types";

export function createDispatchOpacityType(): TextEditorDecorationType {
  return window.createTextEditorDecorationType({ isWholeLine: true, opacity: "0.5" });
}

/** True for a semipy dispatch module path: `.../runtime/<module>.semi.py`. */
export function isDispatchFile(fsPath: string): boolean {
  const norm = fsPath.replace(/\\/g, "/");
  return /\/runtime\/[^/]+\.semi\.py$/.test(norm);
}

function loadPortalForDispatch(fsPath: string): PortalJson | undefined {
  const moduleName = path.basename(fsPath).replace(/\.semi\.py$/, "");
  const cacheDir = path.dirname(path.dirname(fsPath)); // .../<cache>/runtime/x -> <cache>
  let entries: string[];
  try {
    entries = fs.readdirSync(cacheDir).filter((f) => f.endsWith(".portal.json"));
  } catch {
    return undefined;
  }
  for (const f of entries) {
    try {
      const p = JSON.parse(fs.readFileSync(path.join(cacheDir, f), "utf8")) as PortalJson;
      if (p.module_name === moduleName) {
        return p;
      }
    } catch {
      /* skip unreadable portal */
    }
  }
  return undefined;
}

/** Trimmed, non-empty content lines of every committed implementation in the portal. */
function committedLineSet(portal: PortalJson): Set<string> {
  const set = new Set<string>();
  for (const slot of Object.values(portal.slots)) {
    for (const commit of Object.values(slot.commits || {})) {
      const src = commit.generated_source || "";
      for (const raw of src.split(/\r?\n/)) {
        const t = raw.trim();
        if (t) {
          set.add(t);
        }
      }
    }
  }
  return set;
}

/** A line is "generated" if it matches committed code, is a comment, or is trivial scaffold. */
function isGeneratedLine(text: string, committed: Set<string>): boolean {
  const t = text.trim();
  if (!t) {
    return true; // blank lines: leave dim with the surrounding generated body
  }
  if (t.startsWith("#")) {
    return true; // dispatch sketch comments / headers are machine-written
  }
  return committed.has(t);
}

export function refreshDispatchOpacity(editor: TextEditor, dimType: TextEditorDecorationType): void {
  const fsPath = editor.document.uri.fsPath;
  if (!isDispatchFile(fsPath)) {
    editor.setDecorations(dimType, []);
    return;
  }
  const portal = loadPortalForDispatch(fsPath);
  if (!portal) {
    editor.setDecorations(dimType, []);
    return;
  }
  const committed = committedLineSet(portal);
  const dim: Range[] = [];
  const n = editor.document.lineCount;
  for (let i = 0; i < n; i++) {
    const text = editor.document.lineAt(i).text;
    if (isGeneratedLine(text, committed)) {
      dim.push(new Range(i, 0, i, 0));
    }
    // else: user-authored / novel -> no decoration -> full opacity
  }
  editor.setDecorations(dimType, dim);
}
