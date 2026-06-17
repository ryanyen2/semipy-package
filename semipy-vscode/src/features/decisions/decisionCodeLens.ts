/**
 * Inline decision picker (CodeLens). For every slot with an OPEN fork, a row of
 * clickable lenses sits on the slot anchor (the same line as the health lens):
 *
 *   ⎇ multi-part last name   keep all remaining 60%   last word only 40%   Assert…
 *
 * Each fate is a one-click `semipy.pickDecision` (LLM-free head swap); "Assert…"
 * opens `semipy.assertDecision`. The axis label opens the Slot Inspector. This is
 * the "inline widget to choose" surface; the hover (decisionHover.ts) carries the
 * detail. CodeLens cannot be colored, so the at-rest accent lives in the gutter.
 */
import type { CodeLensProvider, TextDocument } from "vscode";
import { CodeLens as VsCodeLens, EventEmitter, Range } from "vscode";
import type { PortalJson } from "../../data/types";
import { resolveSlotUiLines } from "../slotAnnotations/slotLineResolve";
import { DECISION_GLYPH, axisLabel, fateChip, openDecisionsFor } from "./decisionInsight";

export class SemipyDecisionCodeLensProvider implements CodeLensProvider {
  private readonly _onDidChange = new EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChange.event;

  constructor(
    private readonly getPortal: () => PortalJson | undefined,
    private readonly enabled: () => boolean,
  ) {}

  refresh(): void {
    this._onDidChange.fire();
  }

  provideCodeLenses(document: TextDocument): VsCodeLens[] {
    if (!this.enabled()) {
      return [];
    }
    const portal = this.getPortal();
    if (!portal || document.languageId !== "python") {
      return [];
    }
    const out: VsCodeLens[] = [];
    for (const slot of Object.values(portal.slots)) {
      const decisions = openDecisionsFor(slot);
      if (decisions.length === 0) {
        continue;
      }
      const ui = resolveSlotUiLines(document, slot);
      if (!ui || ui.codeLensLine0 >= document.lineCount) {
        continue;
      }
      const range = new Range(ui.codeLensLine0, 0, ui.codeLensLine0, 0);

      // Surface only the highest-consequence open fork inline; the rest stay in
      // the hover so the editor line does not become a wall of lenses.
      const d = decisions[0]!;
      const more = decisions.length > 1 ? `  (+${decisions.length - 1} more)` : "";

      out.push(
        new VsCodeLens(range, {
          title: `${DECISION_GLYPH} ${axisLabel(d)}${more}`,
          command: "semipy.inspectSlot",
          arguments: [slot.slot_id],
        }),
      );
      for (const b of d.branches) {
        out.push(
          new VsCodeLens(range, {
            title: fateChip(b),
            command: "semipy.pickDecision",
            arguments: [slot.slot_id, d.decision_id, b.fate_label],
          }),
        );
      }
      out.push(
        new VsCodeLens(range, {
          title: "Assert…",
          command: "semipy.assertDecision",
          arguments: [slot.slot_id, d.decision_id],
        }),
      );
    }
    return out;
  }
}
