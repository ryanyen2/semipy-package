import * as path from "path";
import type { ExtensionContext, TextEditor } from "vscode";
import {
  Position,
  Range,
  RelativePattern,
  StatusBarAlignment,
  Uri,
  WorkspaceEdit,
  commands,
  languages,
  window,
  workspace,
} from "vscode";
import { findPortalJsonPathForEditor, loadPortalJson } from "./data/portalLoader";
import type { PortalJson, SlotJson } from "./data/types";
import {
  createOpacityDecorationTypes,
  refreshOpacityDecorations,
} from "./features/commentOpacity/opacityDecorations";
import {
  createDispatchOpacityType,
  refreshDispatchOpacity,
} from "./features/commentOpacity/dispatchOpacity";
import {
  SignFlipCoordinator,
  rewriteReasoningPrefixToSpec,
} from "./features/commentOpacity/signFlipListener";
import { activeCommitFromPortalSlot } from "./features/splitEditor/portalCommit";
import {
  createGutterHealthTypes,
  disposeGutterHealthTypes,
  refreshGutterHealth,
} from "./features/health/gutterHealth";
import { computeSlotInsight } from "./features/intelligence/slotInsight";
import { createSlotInsightHoverProvider } from "./features/intelligence/slotInsightHoverProvider";
import { RegressionDiagnosticManager } from "./features/health/regressionDiagnostics";
import {
  createSteeringCodeActionProvider,
  createSteeringHoverProvider,
} from "./features/steering/reasoningSteering";
import { runSteeringModesQuickPick } from "./features/steering/modesControl";
import { createPhraseHoverProvider } from "./features/phraseHighlight/phraseHoverProvider";
import {
  createPhraseDecorationTypes,
  refreshPhraseDecorations,
} from "./features/phraseHighlight/phraseDecorations";
import { LinkedHighlightCoordinator } from "./features/splitEditor/linkedHighlight";
import { openDispatchSplitView } from "./features/splitEditor/splitEditorCommand";
import { SlotHistoryProvider } from "./features/versionTree/slotHistoryProvider";
import {
  registerCommitTextProvider,
  runSemipyCli,
  viewGeneratedCode,
} from "./features/versionTree/versionActions";
import { SemipyDiagnosticManager } from "./features/diagnostics/diagnosticProvider";
import { createRegenerateCodeActionProvider } from "./features/diagnostics/codeActions";
import {
  SemipyCodeLensProvider,
  SemipyInlayHintsProvider,
} from "./features/slotAnnotations/slotEditorAnnotations";
import { getSemipyOutputChannel } from "./logging/semipyOutputChannel";
import {
  createSpecCommentSyntaxTypes,
  disposeSpecCommentSyntaxTypes,
  refreshSpecCommentSyntaxDecorations,
} from "./features/specCommentSyntax/specCommentSyntaxDecorations";

type PortalState = {
  portal: PortalJson | undefined;
  portalPath: string | undefined;
  /** semipy `cache_dir` (parent of `*.portal.json`). */
  portalCacheDir: string | undefined;
  /** Workspace folder used as CLI cwd (interpreter / relative `--portal`). */
  workspaceRoot: string | undefined;
};

function semipyCliFailureMessage(stderr: string, stdout: string, fallback: string): string {
  let detail = (stderr || stdout || fallback).trim().slice(0, 500);
  if (detail.includes("No module named 'semipy'") || detail.includes("No module named semipy")) {
    detail +=
      " Use Python: Select Interpreter for an environment that includes semipy, or set semipy.pythonPath.";
  }
  return detail;
}

/** Return the slot's currently-active branch head (default_branch first,
 * else the most-recent branch head by timestamp). */
function pickActiveHead(slot: SlotJson): string | undefined {
  const defaultHead = slot.branches?.[slot.default_branch]?.head;
  if (defaultHead) {
    return defaultHead;
  }
  const heads = Object.values(slot.branches || {})
    .map((b) => slot.commits[b.head])
    .filter((c): c is NonNullable<typeof c> => !!c);
  heads.sort((a, b) => b.timestamp - a.timestamp);
  return heads[0]?.commit_id;
}

/** Run `semipy rewind-spec` iff the commit carries a source_snapshot. Saves
 * the editor first so VS Code will auto-reload the external file edit. */
async function rewindSpecIfSnapshot(
  editor: TextEditor | undefined,
  slot: SlotJson | undefined,
  slotId: string,
  commitId: string,
  portalRel: string,
  workspaceRoot: string,
): Promise<void> {
  if (!editor) {
    return;
  }
  const snap = slot?.commits?.[commitId]?.source_snapshot;
  if (!snap?.slot_region_text) {
    return;
  }
  if (editor.document.isDirty) {
    await editor.document.save();
  }
  await runSemipyCli(
    ["rewind-spec", "--portal", portalRel, "--slot-id", slotId, "--commit-id", commitId],
    workspaceRoot,
  );
}

function sessionSourceOpts(): { sessionSourceFromSettings?: string } {
  let raw = workspace.getConfiguration("semipy").get<string>("sessionSource")?.trim();
  if (raw?.includes("${workspaceFolder}")) {
    const folder = workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (folder) {
      raw = raw.replace(/\$\{workspaceFolder\}/g, folder);
    }
  }
  return { sessionSourceFromSettings: raw || undefined };
}

function refreshPortalForUri(fsPath: string, state: PortalState): void {
  const found = findPortalJsonPathForEditor(fsPath, sessionSourceOpts());
  if (!found) {
    state.portal = undefined;
    state.portalPath = undefined;
    state.portalCacheDir = undefined;
    state.workspaceRoot = undefined;
    return;
  }
  const portal = loadPortalJson(found);
  if (!portal) {
    state.portal = undefined;
    state.portalPath = undefined;
    state.portalCacheDir = undefined;
    state.workspaceRoot = undefined;
    return;
  }
  state.portalPath = found;
  state.portal = portal;
  state.portalCacheDir = path.dirname(found);
  const wf = workspace.getWorkspaceFolder(Uri.file(found));
  state.workspaceRoot = wf?.uri.fsPath ?? path.dirname(state.portalCacheDir);
}

export function activate(context: ExtensionContext): void {
  const portalState: PortalState = {
    portal: undefined,
    portalPath: undefined,
    portalCacheDir: undefined,
    workspaceRoot: undefined,
  };

  const cfg = () => workspace.getConfiguration("semipy");

  const opacityTypes = createOpacityDecorationTypes();
  const dispatchDimType = createDispatchOpacityType();
  const phraseTypes = createPhraseDecorationTypes();
  const specSyntaxTypes = createSpecCommentSyntaxTypes();
  const gutterTypes = createGutterHealthTypes(context.extensionPath);
  const regressionDiag = new RegressionDiagnosticManager();
  const debounceMs = () => cfg().get<number>("debounceMs") ?? 200;

  /** Active head commit per slot, to detect "what just happened" on portal reload. */
  const lastHeads = new Map<string, string>();
  let headsSeeded = false;

  // const signFlip = false;
  const signFlip = new SignFlipCoordinator(
    () => cfg().get<boolean>("signFlipOnSkeletonEdit") ?? false,
    () => cfg().get<boolean>("signFlipSkipApiEdits") ?? true,
  );

  const codeLensProvider = new SemipyCodeLensProvider(
    () => portalState.portal,
    () => cfg().get<boolean>("enableCodeLens") ?? true,
  );
  const inlayProvider = new SemipyInlayHintsProvider(
    () => portalState.portal,
    () => cfg().get<boolean>("enableInlayHints") ?? true,
  );

  const linked = new LinkedHighlightCoordinator(
    () => cfg().get<number>("linkedHighlightFadeMs") ?? 1500,
  );

  const diag = new SemipyDiagnosticManager(() => portalState.portalCacheDir);

  const tree = new SlotHistoryProvider(() => portalState.portal);
  const treeView = window.createTreeView("semipy.slotHistory", {
    treeDataProvider: tree,
    showCollapseAll: true,
  });

  const status = window.createStatusBarItem(StatusBarAlignment.Left, 100);
  status.command = "semipy.refreshHistory";

  const modes = window.createStatusBarItem(StatusBarAlignment.Left, 99);
  modes.text = "$(settings) Semipy";
  modes.tooltip = "Semipy steering — enable contract / effect gates (scaffolds configure(...))";
  modes.command = "semipy.steeringModes";
  modes.show();

  function refreshAllDecorations(editor: TextEditor | undefined): void {
    if (!editor) {
      return;
    }
    refreshPortalForUri(editor.document.uri.fsPath, portalState);
    const cacheDir = portalState.portalCacheDir;
    refreshOpacityDecorations(editor, opacityTypes.reasoningDim);
    if (cfg().get<boolean>("enableSpecLineSyntax") ?? true) {
      refreshSpecCommentSyntaxDecorations(editor, specSyntaxTypes);
    } else {
      editor.setDecorations(specSyntaxTypes.specMarker, []);
      editor.setDecorations(specSyntaxTypes.specBody, []);
      editor.setDecorations(specSyntaxTypes.reasoningMarker, []);
      editor.setDecorations(specSyntaxTypes.reasoningBody, []);
      editor.setDecorations(specSyntaxTypes.reasoningKeyProvenance, []);
      editor.setDecorations(specSyntaxTypes.reasoningKeyEffect, []);
    }
    if (cfg().get<boolean>("enableGutterHealth") ?? true) {
      refreshGutterHealth(editor, portalState.portal, gutterTypes);
    } else {
      refreshGutterHealth(editor, undefined, gutterTypes);
    }
    if (cfg().get<boolean>("dimGeneratedCode") ?? true) {
      refreshDispatchOpacity(editor, dispatchDimType);
    } else {
      editor.setDecorations(dispatchDimType, []);
    }
    refreshPhraseDecorations(
      editor,
      portalState.portal,
      cacheDir,
      phraseTypes,
      workspace.workspaceFolders?.map((w) => w.uri.fsPath) ?? [],
    );
    const n = portalState.portal ? Object.keys(portalState.portal.slots).length : 0;
    status.text = `Semipy: ${n} slot(s)`;
    status.show();
    tree.refresh();
    diag.refresh();
    codeLensProvider.refresh();
    inlayProvider.refresh();
    linked.onSelectionOrPortal(editor, portalState.portal, cacheDir);
    if (cfg().get<boolean>("notifyOnResolution") ?? true) {
      regressionDiag.refresh(editor, portalState.portal);
    } else {
      regressionDiag.clear();
    }
  }

  /** Focus the slot in the slot-history tree (the persistent "inspector"). */
  async function revealSlot(slotId: string): Promise<void> {
    const ed = window.activeTextEditor;
    if (ed) {
      refreshPortalForUri(ed.document.uri.fsPath, portalState);
    }
    tree.refresh();
    const el = tree.slotElement(slotId);
    if (!el) {
      void window.showWarningMessage("Semipy: that slot is not in the current portal.");
      return;
    }
    try {
      await treeView.reveal(el, { select: true, focus: true, expand: 2 });
    } catch {
      /* tree view may not be ready; ignore */
    }
  }

  /**
   * On portal reload, surface "what just happened": which slots gained a new head
   * commit. Subtle (status-bar message) for clean changes; a toast only when a
   * regression / blocked effect needs attention. Seeds silently on first load.
   */
  function notifyResolutionChanges(portal: PortalJson | undefined): void {
    if (!portal || !(cfg().get<boolean>("notifyOnResolution") ?? true)) {
      return;
    }
    const seenNow = new Map<string, string>();
    const changed: SlotJson[] = [];
    for (const slot of Object.values(portal.slots)) {
      const commit = activeCommitFromPortalSlot(slot);
      if (!commit) {
        continue;
      }
      seenNow.set(slot.slot_id, commit.commit_id);
      const prev = lastHeads.get(slot.slot_id);
      if (headsSeeded && prev !== undefined && prev !== commit.commit_id) {
        changed.push(slot);
      }
    }
    lastHeads.clear();
    for (const [k, v] of seenNow) {
      lastHeads.set(k, v);
    }
    headsSeeded = true;
    for (const slot of changed) {
      const insight = computeSlotInsight(slot);
      if (!insight) {
        continue;
      }
      const fn = slot.slot_spec?.enclosing_function_qualname || slot.function_name_base || "slot";
      if (insight.health === "danger") {
        const n = insight.change?.unintended ?? 0;
        void window
          .showWarningMessage(
            `Semipy ${insight.decision} ${fn} — ${n} unintended regression${n === 1 ? "" : "s"}.`,
            "Inspect",
          )
          .then((pick) => {
            if (pick === "Inspect") {
              void revealSlot(slot.slot_id);
            }
          });
      } else {
        const guarantee = insight.contract.active ? ` · ${insight.contract.active} guarantee(s) hold` : "";
        window.setStatusBarMessage(
          `$(sparkle) Semipy ${insight.glyph} ${insight.decision} ${fn}${guarantee}`,
          6000,
        );
      }
    }
  }

  const opacitySub = subscribeOpacityWrapper(opacityTypes, debounceMs, refreshAllDecorations);

  context.subscriptions.push(
    getSemipyOutputChannel(),
    treeView,
    status,
    modes,
    { dispose: () => disposeGutterHealthTypes(gutterTypes) },
    { dispose: () => dispatchDimType.dispose() },
    regressionDiag,
    { dispose: () => disposeSpecCommentSyntaxTypes(specSyntaxTypes) },
    opacitySub,
    signFlip.attach(),
    { dispose: () => linked.dispose() },
    diag,
    languages.registerCodeLensProvider({ language: "python", scheme: "file" }, codeLensProvider),
    languages.registerInlayHintsProvider({ language: "python", scheme: "file" }, inlayProvider),
    workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("semipy")) {
        codeLensProvider.refresh();
        inlayProvider.refresh();
        refreshAllDecorations(window.activeTextEditor);
      }
    }),
    window.onDidChangeTextEditorSelection((e) => {
      refreshPortalForUri(e.textEditor.document.uri.fsPath, portalState);
      linked.onSelectionOrPortal(e.textEditor, portalState.portal, portalState.portalCacheDir);
    }),
    window.onDidChangeActiveTextEditor((ed) => {
      if (ed) {
        signFlip.seedDocument(ed.document);
      }
      refreshAllDecorations(ed);
    }),
    languages.registerHoverProvider(
      { language: "python", scheme: "file" },
      createPhraseHoverProvider(
        () => portalState.portal,
        () => portalState.portalCacheDir,
        () => workspace.workspaceFolders?.map((w) => w.uri.fsPath) ?? [],
      ),
    ),
    languages.registerHoverProvider(
      { language: "python", scheme: "file" },
      createSlotInsightHoverProvider(
        () => portalState.portal,
        () => cfg().get<boolean>("enableInsightHover") ?? true,
      ),
    ),
    languages.registerHoverProvider(
      { language: "python", scheme: "file" },
      createSteeringHoverProvider(),
    ),
    languages.registerCodeActionsProvider(
      { language: "python", scheme: "file" },
      createSteeringCodeActionProvider(),
    ),
    commands.registerCommand("semipy.steeringModes", () => runSteeringModesQuickPick()),
    commands.registerCommand("semipy.inspectSlot", (slotId: string) => {
      if (slotId) {
        void revealSlot(slotId);
      }
    }),
    commands.registerCommand(
      "semipy.relaxGuarantee",
      async (item: { slot?: SlotJson; guarantee?: { label?: string; caseIds?: string[] } }) => {
        const slotId = item?.slot?.slot_id;
        const caseIds = item?.guarantee?.caseIds ?? [];
        if (!slotId || caseIds.length === 0) {
          void window.showWarningMessage("Semipy: nothing to relax for this guarantee.");
          return;
        }
        const ed = window.activeTextEditor;
        if (ed) {
          refreshPortalForUri(ed.document.uri.fsPath, portalState);
        }
        const root = portalState.workspaceRoot;
        const portalPath = portalState.portalPath;
        if (!root || !portalPath) {
          void window.showErrorMessage("Semipy: no portal for relax.");
          return;
        }
        const ok = await window.showWarningMessage(
          `Relax guarantee "${item.guarantee?.label}"? It will be quarantined (kept for audit, no longer enforced) across ${caseIds.length} input pattern(s).`,
          { modal: true },
          "Relax",
        );
        if (ok !== "Relax") {
          return;
        }
        const rel = path.relative(root, portalPath);
        const r = await runSemipyCli(
          ["quarantine-cases", "--portal", rel, "--slot-id", slotId, "--case-ids", caseIds.join(",")],
          root,
        );
        if (r.code !== 0 && r.code !== null) {
          void window.showErrorMessage(
            `Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "relax failed")}`,
          );
          return;
        }
        void window.showInformationMessage((r.stdout || r.stderr || "Guarantee relaxed.").trim().slice(0, 200));
        refreshAllDecorations(window.activeTextEditor);
      },
    ),
    commands.registerCommand("semipy.viewActiveCode", async (slotId: string) => {
      const ed = window.activeTextEditor;
      const fsPath = ed?.document.uri.fsPath;
      const portalPath =
        (fsPath && findPortalJsonPathForEditor(fsPath, sessionSourceOpts())) || portalState.portalPath;
      const portal = portalPath ? loadPortalJson(portalPath) : portalState.portal;
      const slot = portal?.slots[slotId];
      const commit = slot ? activeCommitFromPortalSlot(slot) : undefined;
      const src = commit?.generated_source;
      if (!slot || !commit || !src) {
        void window.showWarningMessage("Semipy: no active implementation found for this slot.");
        return;
      }
      await viewGeneratedCode(slotId, commit.commit_id, src);
    }),
    commands.registerCommand("semipy.revertEffectTreeItem", (item: { slot?: SlotJson; event?: { event_id?: string } }) => {
      if (item?.slot?.slot_id && item.event?.event_id) {
        return commands.executeCommand("semipy.revertEffect", item.slot.slot_id, item.event.event_id);
      }
      return undefined;
    }),
    commands.registerCommand("semipy.revertEffect", async (slotId: string, eventId: string) => {
      const ed = window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!root || !portalPath || !slotId || !eventId) {
        void window.showErrorMessage("Semipy: no portal / event for revert.");
        return;
      }
      const ok = await window.showWarningMessage(
        `Revert this applied effect? semipy will replay its stored compensations (exact inverse of what was done).`,
        { modal: true },
        "Revert",
      );
      if (ok !== "Revert") {
        return;
      }
      const rel = path.relative(root, portalPath);
      const r = await runSemipyCli(
        ["revert-effect", "--portal", rel, "--slot-id", slotId, "--event-id", eventId],
        root,
      );
      if (r.code !== 0 && r.code !== null) {
        void window.showErrorMessage(`Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "revert failed")}`);
        return;
      }
      void window.showInformationMessage((r.stdout || r.stderr || "Effect reverted.").trim().slice(0, 300));
      refreshAllDecorations(window.activeTextEditor);
    }),
    commands.registerCommand("semipy.promoteReasoningLine", async (uriArg: Uri | string, line0: number) => {
      const uri = typeof uriArg === "string" ? Uri.parse(uriArg) : uriArg;
      const doc = await workspace.openTextDocument(uri);
      if (line0 < 0 || line0 >= doc.lineCount) {
        return;
      }
      const line = doc.lineAt(line0);
      const fixed = rewriteReasoningPrefixToSpec(line.text);
      if (fixed === null || fixed === line.text) {
        void window.showInformationMessage("Semipy: that line is not an inferred (#<) note.");
        return;
      }
      const edit = new WorkspaceEdit();
      edit.replace(uri, line.range, fixed);
      await workspace.applyEdit(edit);
      window.setStatusBarMessage("$(pin) Semipy: pinned as contract (#>) — re-run to honour it.", 5000);
    }),
    commands.registerCommand("semipy.dismissReasoningLine", async (uriArg: Uri | string, line0: number) => {
      const uri = typeof uriArg === "string" ? Uri.parse(uriArg) : uriArg;
      const doc = await workspace.openTextDocument(uri);
      if (line0 < 0 || line0 >= doc.lineCount) {
        return;
      }
      const start = new Position(line0, 0);
      const end =
        line0 + 1 < doc.lineCount ? new Position(line0 + 1, 0) : doc.lineAt(line0).range.end;
      const edit = new WorkspaceEdit();
      edit.delete(uri, new Range(start, end));
      await workspace.applyEdit(edit);
    }),
    registerCommitTextProvider(),
    commands.registerCommand("semipy.noop", () => { }),
    commands.registerCommand("semipy.showOutput", () => {
      getSemipyOutputChannel().show(true);
    }),
    commands.registerCommand("semipy.openSplitView", async () => {
      // Resolve a portal even when no text editor is focused -- e.g. when invoked
      // from the Slot Inspector webview, window.activeTextEditor is undefined.
      const ed = window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      if (!portalState.portal || !portalState.portalCacheDir) {
        for (const v of window.visibleTextEditors) {
          if (v.document.languageId !== "python") {
            continue;
          }
          refreshPortalForUri(v.document.uri.fsPath, portalState);
          if (portalState.portal) {
            break;
          }
        }
      }
      if (!portalState.portal || !portalState.portalCacheDir) {
        void window.showWarningMessage(
          "Semipy: no portal resolved. Focus the Python file that owns this slot, then try again.",
        );
        return;
      }
      await openDispatchSplitView(portalState.portalCacheDir, portalState.portal.module_name);
    }),
    commands.registerCommand("semipy.refreshHistory", () => {
      const ed = window.activeTextEditor;
      refreshAllDecorations(ed);
      tree.refresh();
    }),
    commands.registerCommand("semipy.pickSlotVersion", async (slotId: string) => {
      const ed = window.activeTextEditor;
      if (!ed) {
        return;
      }
      refreshPortalForUri(ed.document.uri.fsPath, portalState);
      const portal = portalState.portal;
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!portal || !root || !portalPath) {
        void window.showWarningMessage("Semipy: no portal for this file.");
        return;
      }
      const slot = portal.slots[slotId];
      if (!slot) {
        return;
      }
      const commits = Object.values(slot.commits).sort((a, b) => b.timestamp - a.timestamp);
      const picked = await window.showQuickPick(
        commits.map((c) => ({
          label: c.commit_id.slice(0, 8),
          description: `${c.decision} ${(c.message || "").slice(0, 48)}`,
          detail: new Date(c.timestamp * 1000).toLocaleString(),
          cid: c.commit_id,
        })),
        { placeHolder: "Activate commit (rollback branch head to here)" },
      );
      if (!picked || !("cid" in picked)) {
        return;
      }
      const rel = path.relative(root, portalPath);
      const r = await runSemipyCli(
        ["rollback", "--portal", rel, "--slot-id", slotId, "--commit-id", picked.cid],
        root,
      );
      if (r.code !== 0 && r.code !== null) {
        void window.showErrorMessage(
          `Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "rollback failed")}`,
        );
        return;
      }
      const chosen = slot.commits[picked.cid];
      if (chosen?.source_snapshot?.slot_region_text) {
        await rewindSpecIfSnapshot(ed, slot, slotId, picked.cid, rel, root);
      } else {
        void window.showInformationMessage(
          "Semipy: rollback complete (legacy commit has no spec snapshot; source file unchanged).",
        );
      }
      refreshAllDecorations(ed);
    }),
    commands.registerCommand("semipy.lockSlotVersion", async (slotId: string, commitId: string) => {
      const ed = window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!root || !portalPath || !commitId) {
        void window.showErrorMessage("Semipy: no portal or commit for lock.");
        return;
      }
      const rel = path.relative(root, portalPath);
      const r = await runSemipyCli(
        ["lock", "--portal", rel, "--slot-id", slotId, "--commit-id", commitId],
        root,
      );
      if (r.code !== 0 && r.code !== null) {
        void window.showErrorMessage(`Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "lock failed")}`);
        return;
      }
      const lockedSlot = portalState.portal?.slots[slotId];
      await rewindSpecIfSnapshot(ed, lockedSlot, slotId, commitId, rel, root);
      void window.showInformationMessage((r.stderr || r.stdout || "Lock saved.").trim().slice(0, 400));
      refreshAllDecorations(window.activeTextEditor);
    }),
    commands.registerCommand("semipy.unlockSlotVersion", async (slotId: string) => {
      const ed = window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!root || !portalPath) {
        void window.showWarningMessage("Semipy: no portal for this file.");
        return;
      }
      const rel = path.relative(root, portalPath);
      const r = await runSemipyCli(["unlock", "--portal", rel, "--slot-id", slotId], root);
      if (r.code !== 0 && r.code !== null) {
        void window.showErrorMessage(`Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "unlock failed")}`);
        return;
      }
      // After unlock, the active head changes; rewind the source to whatever
      // is now active (most-recent branch head with a snapshot) so the editor
      // matches the dispatch module.
      refreshPortalForUri(ed?.document.uri.fsPath ?? "", portalState);
      const unlockedSlot = portalState.portal?.slots[slotId];
      const activeHead = unlockedSlot ? pickActiveHead(unlockedSlot) : undefined;
      if (activeHead) {
        await rewindSpecIfSnapshot(ed, unlockedSlot, slotId, activeHead, rel, root);
      }
      void window.showInformationMessage((r.stderr || r.stdout || "Unlocked.").trim().slice(0, 400));
      refreshAllDecorations(window.activeTextEditor);
    }),
    commands.registerCommand(
      "semipy.viewGeneratedCode",
      async (slotId: string, commitId: string) => {
        const ed = window.activeTextEditor;
        const fsPath = ed?.document.uri.fsPath;
        const portalPath =
          (fsPath && findPortalJsonPathForEditor(fsPath, sessionSourceOpts())) ||
          portalState.portalPath;
        const portal = portalPath ? loadPortalJson(portalPath) : portalState.portal;
        const slot = portal?.slots[slotId];
        const src = slot?.commits[commitId]?.generated_source;
        if (!src) {
          void window.showWarningMessage(
            "Semipy: commit source not loaded. Refresh history or open the source file that owns this portal.",
          );
          return;
        }
        await viewGeneratedCode(slotId, commitId, src);
      },
    ),
    commands.registerCommand(
      "semipy.regenerateSlotDiagnostic",
      async (ws: string, portalRel: string, slotId: string) => {
        const r = await runSemipyCli(
          ["regenerate", "--portal", portalRel, "--slot-id", slotId],
          ws,
        );
        if (r.code !== 0 && r.code !== null) {
          void window.showErrorMessage(
            `Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "regenerate failed")}`,
          );
          return;
        }
        void window.showInformationMessage(r.stderr || r.stdout || "semipy regenerate finished.");
        diag.refresh();
      },
    ),
    languages.registerCodeActionsProvider(
      { language: "python", scheme: "file" },
      createRegenerateCodeActionProvider(
        () => portalState.workspaceRoot,
        () =>
          portalState.portalPath && portalState.workspaceRoot
            ? path.relative(portalState.workspaceRoot, portalState.portalPath)
            : undefined,
      ),
    ),
  );

  const ed0 = window.activeTextEditor;
  if (ed0) {
    signFlip.seedDocument(ed0.document);
  }
  refreshAllDecorations(ed0);
  notifyResolutionChanges(portalState.portal); // seed head map silently

  if (workspace.workspaceFolders?.length) {
    const wf = workspace.workspaceFolders[0]!.uri.fsPath;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const fire = () => {
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(() => {
        timer = undefined;
        refreshAllDecorations(window.activeTextEditor);
        notifyResolutionChanges(portalState.portal);
      }, debounceMs());
    };
    const wPortal = workspace.createFileSystemWatcher(new RelativePattern(wf, "**/*.portal.json"));
    const wSemi = workspace.createFileSystemWatcher(new RelativePattern(wf, "**/*.semi.py"));
    wPortal.onDidChange(fire);
    wPortal.onDidCreate(fire);
    wPortal.onDidDelete(fire);
    wSemi.onDidChange(fire);
    wSemi.onDidCreate(fire);
    wSemi.onDidDelete(fire);
    context.subscriptions.push(wPortal, wSemi);
  }
}

function subscribeOpacityWrapper(
  types: ReturnType<typeof createOpacityDecorationTypes>,
  debounceMs: () => number,
  onRefresh: (ed: TextEditor | undefined) => void,
): { dispose: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const tick = () => {
    onRefresh(window.activeTextEditor);
  };
  const sub1 = window.onDidChangeActiveTextEditor(() => {
    tick();
  });
  const sub2 = workspace.onDidChangeTextDocument((ev) => {
    const ed = window.activeTextEditor;
    if (!ed || ev.document !== ed.document) {
      return;
    }
    if (timer) {
      clearTimeout(timer);
    }
    timer = setTimeout(() => {
      timer = undefined;
      tick();
    }, debounceMs());
  });
  tick();
  return {
    dispose: () => {
      sub1.dispose();
      sub2.dispose();
      if (timer) {
        clearTimeout(timer);
      }
      types.reasoningDim.dispose();
    },
  };
}

export function deactivate(): void { }
