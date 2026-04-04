import type { TextDocument } from "vscode";
import type { SlotJson } from "../../data/types";
import { pathsEqualRobust } from "../../data/portalLoader";
import { resolveSourceBlockRange } from "../splitEditor/correspondenceMap";

/**
 * Map portal line metadata to current editor lines using spec_text / buffer content
 * so CodeLens and inlays stay aligned when the user inserts lines above a slot.
 */
export function resolveSlotUiLines(
  document: TextDocument,
  slot: SlotJson,
): { codeLensLine0: number; inlayLine0: number } | undefined {
  const spec = slot.slot_spec;
  if (!spec) {
    return undefined;
  }
  const fsPath = document.uri.fsPath;
  const src = spec.source_span;
  if (!Array.isArray(src) || src.length < 3) {
    return undefined;
  }
  const [fn, start1] = src as [string, number, number];
  if (!pathsEqualRobust(fn, fsPath)) {
    return undefined;
  }

  const fullText = document.getText();
  const block = resolveSourceBlockRange(fullText, slot);
  if (!block) {
    return undefined;
  }
  const { startLine1, endLine1 } = block;
  const lines = fullText.split(/\r?\n/);

  let inlayLine1 = startLine1;
  for (let i = startLine1 - 1; i <= endLine1 - 1 && i < lines.length; i++) {
    const raw = lines[i] ?? "";
    if (/#\s*>/.test(raw)) {
      inlayLine1 = i + 1;
      break;
    }
  }

  const start0 = startLine1 - 1;
  let semiformalLine: number | undefined;
  let defLine: number | undefined;
  for (let i = start0; i >= 0 && i >= start0 - 200; i--) {
    const t = document.lineAt(i).text.trim();
    if (t.startsWith("@semiformal")) {
      semiformalLine = i;
    }
    if (t.startsWith("def ") || t.startsWith("async def")) {
      defLine = i;
    }
  }
  const codeLensLine0 = semiformalLine ?? defLine ?? Math.max(0, start1 - 1);
  return { codeLensLine0, inlayLine0: inlayLine1 - 1 };
}
