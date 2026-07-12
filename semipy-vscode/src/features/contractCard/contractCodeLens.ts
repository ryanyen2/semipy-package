/**
 * Contract state CodeLens. For every committed slot with a contentful contract
 * surface, one quiet line sits on the slot anchor (the same line the health
 * lens and decision lenses use):
 *
 *   § plastic  ·  3 active cases  ·  scope minted  ·  2 regimes    Dispute…
 *
 * The line is the same four facts as the hover's headline (hardness, case
 * count, scope status, regime count) -- none of which `#<` already renders
 * (KTD-3). "Dispute…" opens `semipy.disputeContract`. Skips plain functions
 * and generated-but-uncontracted slots (`hasContractSurface`).
 */
import type { CodeLensProvider, TextDocument } from "vscode";
import { CodeLens as VsCodeLens, EventEmitter, Range } from "vscode";
import type { PortalJson } from "../../data/types";
import { resolveSlotUiLines } from "../slotAnnotations/slotLineResolve";
import { codeLensTitle, hasContractSurface } from "./contractInsight";

export class SemipyContractCodeLensProvider implements CodeLensProvider {
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
      if (!hasContractSurface(slot)) {
        continue;
      }
      const ui = resolveSlotUiLines(document, slot);
      if (!ui || ui.codeLensLine0 >= document.lineCount) {
        continue;
      }
      const range = new Range(ui.codeLensLine0, 0, ui.codeLensLine0, 0);
      out.push(
        new VsCodeLens(range, {
          title: codeLensTitle(slot),
          command: "semipy.inspectSlot",
          arguments: [slot.slot_id],
        }),
      );
      out.push(
        new VsCodeLens(range, {
          title: "Dispute…",
          command: "semipy.disputeContract",
          arguments: [slot.slot_id],
        }),
      );
    }
    return out;
  }
}
