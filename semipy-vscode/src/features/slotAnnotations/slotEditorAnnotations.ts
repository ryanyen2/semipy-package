import * as path from "path";
import type {
  CancellationToken,
  CodeLensProvider,
  InlayHintsProvider,
  TextDocument,
} from "vscode";
import {
  CodeLens as VsCodeLens,
  EventEmitter,
  InlayHint as VsInlayHint,
  InlayHintKind,
  Position,
  Range,
} from "vscode";
import type { PortalJson, SlotSpecJson } from "../../data/types";
import { pathsEqualRobust } from "../../data/portalLoader";
import { activeCommitFromPortalSlot } from "../splitEditor/portalCommit";
import { computeSlotInsight, decisionGlyph, insightChips } from "../intelligence/slotInsight";
import { resolveSlotUiLines } from "./slotLineResolve";

/** Fallback when spec_text / buffer resolution fails (stale portal-only lines). */
function codeLensLineIndexStale(doc: TextDocument, spec: SlotSpecJson | null | undefined): number | undefined {
  if (!spec) {
    return undefined;
  }
  const fsPath = doc.uri.fsPath;
  const enc = spec.enclosing_function_span;
  if (Array.isArray(enc) && enc.length >= 2) {
    const [fn, start1] = enc as [string, number, number];
    if (pathsEqualRobust(fn, fsPath)) {
      return Math.max(0, start1 - 1);
    }
  }
  const src = spec.source_span;
  if (!Array.isArray(src) || src.length < 2) {
    return undefined;
  }
  const [fn, start1] = src as [string, number, number];
  if (!pathsEqualRobust(fn, fsPath)) {
    return undefined;
  }
  const firstSpecLine = Math.max(0, start1 - 1);
  // Walk UP and stop at the FIRST (nearest) @semiformal / def — never
  // continue overwriting, otherwise slots always collapse onto the topmost
  // @semiformal in the file.
  let semiformalLine: number | undefined;
  let defLine: number | undefined;
  for (let i = firstSpecLine; i >= 0 && i >= firstSpecLine - 120; i--) {
    const t = doc.lineAt(i).text.trim();
    if (semiformalLine === undefined && t.startsWith("@semiformal")) {
      semiformalLine = i;
      break;
    }
    if (defLine === undefined && (t.startsWith("def ") || t.startsWith("async def"))) {
      defLine = i;
    }
  }
  return semiformalLine ?? defLine ?? Math.max(0, firstSpecLine - 1);
}

export class SemipyCodeLensProvider implements CodeLensProvider {
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
    const fsPath = document.uri.fsPath;
    const out: VsCodeLens[] = [];
    for (const slot of Object.values(portal.slots)) {
      // Skip phantom slots (no commits) so stale ordinal-drifted entries don't
      // stack extra "Switch version" / "Lock" buttons on top of live slots.
      if (!slot.commits || Object.keys(slot.commits).length === 0) {
        continue;
      }
      const spec = slot.slot_spec;
      const ui = resolveSlotUiLines(document, slot);
      const lineIdx = ui?.codeLensLine0 ?? codeLensLineIndexStale(document, spec);
      if (lineIdx === undefined || lineIdx >= document.lineCount) {
        continue;
      }
      const range = new Range(lineIdx, 0, lineIdx, 0);
      const commit = activeCommitFromPortalSlot(slot);
      const insight = computeSlotInsight(slot);
      const locked = !!slot.refs?.["__locked__"];

      // The one-line health sentence. Clicking it opens the Slot Inspector.
      const headline = insight ? insightChips(insight).join(" · ") : "Semipy slot";
      out.push(
        new VsCodeLens(range, {
          title: headline,
          command: "semipy.inspectSlot",
          arguments: [slot.slot_id],
        }),
      );

      // Action lenses, present only when actionable (minimum-set rule).
      if (Object.keys(slot.commits).length > 1) {
        out.push(
          new VsCodeLens(range, {
            title: "Versions",
            command: "semipy.pickSlotVersion",
            arguments: [slot.slot_id],
          }),
        );
      }
      out.push(
        new VsCodeLens(range, {
          title: locked ? "Unlock" : "Lock",
          command: locked ? "semipy.unlockSlotVersion" : "semipy.lockSlotVersion",
          arguments: locked ? [slot.slot_id] : [slot.slot_id, commit?.commit_id ?? ""],
        }),
      );
      if (insight && insight.effect.applied > 0 && insight.effect.latestEventId) {
        out.push(
          new VsCodeLens(range, {
            title: "Revert effect",
            command: "semipy.revertEffect",
            arguments: [slot.slot_id, insight.effect.latestEventId],
          }),
        );
      }
    }
    return out;
  }
}

export class SemipyInlayHintsProvider implements InlayHintsProvider {
  private readonly _onDidChange = new EventEmitter<void>();
  readonly onDidChangeInlayHints = this._onDidChange.event;

  constructor(
    private readonly getPortal: () => PortalJson | undefined,
    private readonly enabled: () => boolean,
  ) {}

  refresh(): void {
    this._onDidChange.fire();
  }

  provideInlayHints(
    document: TextDocument,
    _range: Range,
    _token: CancellationToken,
  ): VsInlayHint[] | undefined {
    if (!this.enabled()) {
      return undefined;
    }
    const portal = this.getPortal();
    if (!portal || document.languageId !== "python") {
      return undefined;
    }
    const fsPath = document.uri.fsPath;
    const hints: VsInlayHint[] = [];
    for (const slot of Object.values(portal.slots)) {
      if (!slot.commits || Object.keys(slot.commits).length === 0) {
        continue;
      }
      const spec = slot.slot_spec;
      const src = spec?.source_span;
      if (!Array.isArray(src) || src.length < 3) {
        continue;
      }
      const [fn] = src as [string, number, number];
      if (!pathsEqualRobust(fn, fsPath)) {
        continue;
      }
      const ui = resolveSlotUiLines(document, slot);
      const lineNo = ui?.inlayLine0 ?? Math.max(0, (src[1] as number) - 1);
      if (lineNo >= document.lineCount) {
        continue;
      }
      const line = document.lineAt(lineNo);
      const commit = activeCommitFromPortalSlot(slot);
      const decision = (commit?.decision || "?").toUpperCase();
      const idShort = commit?.commit_id?.slice(0, 8) ?? "?";
      const label = ` ${decisionGlyph(commit?.decision || "")} ${decision} · ${idShort} `;
      hints.push(
        new VsInlayHint(
          new Position(lineNo, line.text.length),
          label,
          InlayHintKind.Type,
        ),
      );
    }
    return hints.length ? hints : undefined;
  }
}
