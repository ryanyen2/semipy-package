/**
 * The ambient gutter glyph: one small icon per slot, colored by health, so you
 * can scan a file and know which slots are clean, which touch the world, and
 * which need attention -- without reading any text. Priority danger > warn >
 * effect > ok (computed in slotInsight). Right-edge overview ruler mirrors it.
 */
import * as path from "path";
import type { TextEditor, TextEditorDecorationType } from "vscode";
import { OverviewRulerLane, Range, Uri, window } from "vscode";
import type { PortalJson, SlotJson } from "../../data/types";
import { pathsEqualRobust } from "../../data/portalLoader";
import { resolveSlotUiLines } from "../slotAnnotations/slotLineResolve";
import { computeSlotInsight, type SlotHealth } from "../intelligence/slotInsight";

export interface GutterHealthTypes {
  ok: TextEditorDecorationType;
  effect: TextEditorDecorationType;
  warn: TextEditorDecorationType;
  danger: TextEditorDecorationType;
}

function icon(extensionPath: string, name: string): Uri {
  return Uri.file(path.join(extensionPath, "images", "gutter", `slot-${name}.svg`));
}

export function createGutterHealthTypes(extensionPath: string): GutterHealthTypes {
  const make = (name: SlotHealth, ruler?: string): TextEditorDecorationType =>
    window.createTextEditorDecorationType({
      gutterIconPath: icon(extensionPath, name),
      gutterIconSize: "contain",
      ...(ruler
        ? { overviewRulerColor: ruler, overviewRulerLane: OverviewRulerLane.Right }
        : {}),
    });
  return {
    ok: make("ok"),
    effect: make("effect", "rgba(215,166,95,0.55)"),
    warn: make("warn", "rgba(215,166,95,0.85)"),
    danger: make("danger", "rgba(209,105,105,0.9)"),
  };
}

export function refreshGutterHealth(
  editor: TextEditor,
  portal: PortalJson | undefined,
  types: GutterHealthTypes,
): void {
  const clear = () => {
    editor.setDecorations(types.ok, []);
    editor.setDecorations(types.effect, []);
    editor.setDecorations(types.warn, []);
    editor.setDecorations(types.danger, []);
  };
  if (!portal || editor.document.languageId !== "python") {
    clear();
    return;
  }
  const fsPath = editor.document.uri.fsPath;
  const buckets: Record<SlotHealth, Range[]> = { ok: [], effect: [], warn: [], danger: [] };

  for (const slot of Object.values(portal.slots) as SlotJson[]) {
    if (!slot.commits || Object.keys(slot.commits).length === 0) {
      continue;
    }
    const src = slot.slot_spec?.source_span;
    if (!Array.isArray(src) || src.length < 3 || !pathsEqualRobust(src[0] as string, fsPath)) {
      continue;
    }
    const ui = resolveSlotUiLines(editor.document, slot);
    const line0 = ui?.codeLensLine0;
    if (line0 === undefined || line0 >= editor.document.lineCount) {
      continue;
    }
    const insight = computeSlotInsight(slot);
    if (!insight) {
      continue;
    }
    buckets[insight.health].push(new Range(line0, 0, line0, 0));
  }

  editor.setDecorations(types.ok, buckets.ok);
  editor.setDecorations(types.effect, buckets.effect);
  editor.setDecorations(types.warn, buckets.warn);
  editor.setDecorations(types.danger, buckets.danger);
}

export function disposeGutterHealthTypes(types: GutterHealthTypes): void {
  types.ok.dispose();
  types.effect.dispose();
  types.warn.dispose();
  types.danger.dispose();
}
