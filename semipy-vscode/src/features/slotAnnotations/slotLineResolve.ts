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
  // Prefer the slot's own enclosing_function_span start (authoritative per slot).
  // Each slot carries its enclosing function's range in the portal, so using it
  // guarantees that slots in different @semiformal methods in the same file get
  // distinct, correct CodeLens anchors instead of collapsing onto the topmost
  // @semiformal in the file.
  const enc = spec.enclosing_function_span;
  let preferredAnchor: number | undefined;
  if (Array.isArray(enc) && enc.length >= 2) {
    const encPath = enc[0];
    const encStart1 = Number(enc[1]);
    if (
      typeof encPath === "string" &&
      pathsEqualRobust(encPath, fsPath) &&
      Number.isFinite(encStart1) &&
      encStart1 >= 1 &&
      encStart1 - 1 < document.lineCount
    ) {
      preferredAnchor = encStart1 - 1;
    }
  }

  // Fallback: walk UP from the slot's source span and stop at the FIRST
  // (nearest) @semiformal or `def` — do NOT continue and overwrite with older
  // matches, otherwise every slot in the file lands on the topmost @semiformal.
  let semiformalLine: number | undefined;
  let defLine: number | undefined;
  if (preferredAnchor === undefined) {
    for (let i = start0; i >= 0 && i >= start0 - 200; i--) {
      const t = document.lineAt(i).text.trim();
      if (semiformalLine === undefined && t.startsWith("@semiformal")) {
        semiformalLine = i;
        break;
      }
      if (defLine === undefined && (t.startsWith("def ") || t.startsWith("async def"))) {
        defLine = i;
      }
    }
  }
  const codeLensLine0 =
    preferredAnchor ?? semiformalLine ?? defLine ?? Math.max(0, start1 - 1);
  return { codeLensLine0, inlayLine0: inlayLine1 - 1 };
}
