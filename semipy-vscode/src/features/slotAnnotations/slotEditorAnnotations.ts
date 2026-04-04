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
  let semiformalLine: number | undefined;
  let defLine: number | undefined;
  for (let i = firstSpecLine; i >= 0 && i >= firstSpecLine - 120; i--) {
    const t = doc.lineAt(i).text.trim();
    if (t.startsWith("@semiformal")) {
      semiformalLine = i;
    }
    if (t.startsWith("def ") || t.startsWith("async def")) {
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
      const spec = slot.slot_spec;
      const ui = resolveSlotUiLines(document, slot);
      const lineIdx = ui?.codeLensLine0 ?? codeLensLineIndexStale(document, spec);
      if (lineIdx === undefined || lineIdx >= document.lineCount) {
        continue;
      }
      const range = new Range(lineIdx, 0, lineIdx, 0);
      const commit = activeCommitFromPortalSlot(slot);
      const idShort = commit?.commit_id?.slice(0, 8) ?? "?";
      const decision = (commit?.decision || "?").toUpperCase();
      const t = commit?.timestamp
        ? new Date(commit.timestamp * 1000).toLocaleString()
        : "";
      const locked = slot.refs?.["__locked__"];
      const headline = locked
        ? `Semipy locked · ${idShort} · ${t}`
        : `Semipy ${decision} · ${idShort} · ${t}`;

      out.push(
        new VsCodeLens(range, {
          title: headline,
          command: "semipy.noop",
        }),
        new VsCodeLens(range, {
          title: "Switch version",
          command: "semipy.pickSlotVersion",
          arguments: [slot.slot_id],
        }),
        new VsCodeLens(range, {
          title: locked ? "Unlock" : "Lock",
          command: locked ? "semipy.unlockSlotVersion" : "semipy.lockSlotVersion",
          arguments: locked ? [slot.slot_id] : [slot.slot_id, commit?.commit_id ?? ""],
        }),
      );
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
      const label = ` semipy · ${decision} · ${idShort} `;
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
