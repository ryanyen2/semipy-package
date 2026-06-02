/**
 * Regressions as diagnostics. When semipy regenerates a slot and the effect-diff
 * flags an *unintended* change, that belongs in the persistent attention queue
 * (Problems panel + squiggle), not a transient toast. This is the "needs
 * attention" surface: it survives until the regression is resolved (re-pin,
 * roll back, or accept), and it points at the exact slot line.
 */
import type { TextEditor } from "vscode";
import { Diagnostic, DiagnosticSeverity, Range, languages } from "vscode";
import type { DiagnosticCollection } from "vscode";
import type { PortalJson, SlotJson } from "../../data/types";
import { pathsEqualRobust } from "../../data/portalLoader";
import { resolveSlotUiLines } from "../slotAnnotations/slotLineResolve";
import { computeSlotInsight } from "../intelligence/slotInsight";

function trunc(s: string, n: number): string {
  const t = (s || "").replace(/\s+/g, " ").trim();
  return t.length <= n ? t : t.slice(0, n - 1) + "…";
}

export class RegressionDiagnosticManager {
  private readonly collection: DiagnosticCollection;

  constructor() {
    this.collection = languages.createDiagnosticCollection("semipy-insight");
  }

  clear(): void {
    this.collection.clear();
  }

  /** Recompute regression diagnostics for the active editor's file. */
  refresh(editor: TextEditor | undefined, portal: PortalJson | undefined): void {
    if (!editor || !portal || editor.document.languageId !== "python") {
      return;
    }
    const fsPath = editor.document.uri.fsPath;
    const diags: Diagnostic[] = [];

    for (const slot of Object.values(portal.slots) as SlotJson[]) {
      if (!slot.commits || Object.keys(slot.commits).length === 0) {
        continue;
      }
      const src = slot.slot_spec?.source_span;
      if (!Array.isArray(src) || src.length < 3 || !pathsEqualRobust(src[0] as string, fsPath)) {
        continue;
      }
      const insight = computeSlotInsight(slot);
      const ch = insight?.change;
      if (!ch || !ch.hasRegression) {
        continue;
      }
      const ui = resolveSlotUiLines(editor.document, slot);
      const line0 = ui?.inlayLine0 ?? ui?.codeLensLine0;
      if (line0 === undefined || line0 >= editor.document.lineCount) {
        continue;
      }
      const line = editor.document.lineAt(line0);
      const example = ch.diffs.find((d) => !d.intended);
      const detail = example
        ? ` e.g. \`${trunc(example.oldRepr, 28)}\` → \`${trunc(example.newRepr, 28)}\``
        : "";
      const msg =
        `semipy: ${insight.decision} introduced ${ch.unintended} unintended ` +
        `change${ch.unintended === 1 ? "" : "s"} (${ch.intended} intended).${detail}`;
      const d = new Diagnostic(line.range, msg, DiagnosticSeverity.Warning);
      d.source = "semipy";
      d.code = `semi-regression:${slot.slot_id}`;
      diags.push(d);
    }

    this.collection.set(editor.document.uri, diags);
  }

  dispose(): void {
    this.collection.dispose();
  }
}
