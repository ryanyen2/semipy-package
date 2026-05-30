import * as path from "path";
import type { ExtensionContext, TextEditor } from "vscode";
import {
  RelativePattern,
  StatusBarAlignment,
  Uri,
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
import { SignFlipCoordinator } from "./features/commentOpacity/signFlipListener";
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
  const phraseTypes = createPhraseDecorationTypes();
  const specSyntaxTypes = createSpecCommentSyntaxTypes();
  const debounceMs = () => cfg().get<number>("debounceMs") ?? 200;

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
  }

  const opacitySub = subscribeOpacityWrapper(opacityTypes, debounceMs, refreshAllDecorations);

  context.subscriptions.push(
    getSemipyOutputChannel(),
    treeView,
    status,
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
    registerCommitTextProvider(),
    commands.registerCommand("semipy.noop", () => { }),
    commands.registerCommand("semipy.showOutput", () => {
      getSemipyOutputChannel().show(true);
    }),
    commands.registerCommand("semipy.openSplitView", async () => {
      const ed = window.activeTextEditor;
      if (!ed) {
        return;
      }
      refreshPortalForUri(ed.document.uri.fsPath, portalState);
      if (!portalState.portal || !portalState.portalCacheDir) {
        void window.showWarningMessage("Semipy: no portal for this file.");
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
