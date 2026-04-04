import * as path from "path";
import type { ExtensionContext, TextEditor } from "vscode";
import {
  RelativePattern,
  StatusBarAlignment,
  commands,
  languages,
  window,
  workspace,
} from "vscode";
import {
  expectedPortalJsonPath,
  findWorkspaceRootContainingSemiformal,
  loadPortalJson,
  portalMatchesEditorFile,
} from "./data/portalLoader";
import type { PortalJson } from "./data/types";
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
  viewGeneratedCode,
  runSemipyCli,
} from "./features/versionTree/versionActions";
import { SemipyDiagnosticManager } from "./features/diagnostics/diagnosticProvider";
import { createRegenerateCodeActionProvider } from "./features/diagnostics/codeActions";

type PortalState = {
  portal: PortalJson | undefined;
  portalPath: string | undefined;
  workspaceRoot: string | undefined;
};

function refreshPortalForUri(fsPath: string, state: PortalState): void {
  state.workspaceRoot = findWorkspaceRootContainingSemiformal(fsPath);
  if (!state.workspaceRoot) {
    state.portal = undefined;
    state.portalPath = undefined;
    return;
  }
  const expected = expectedPortalJsonPath(fsPath);
  if (!expected) {
    state.portal = undefined;
    state.portalPath = undefined;
    return;
  }
  state.portalPath = expected;
  state.portal = loadPortalJson(expected);
  if (state.portal && !portalMatchesEditorFile(state.portal, fsPath)) {
    state.portal = undefined;
  }
}

export function activate(context: ExtensionContext): void {
  const portalState: PortalState = {
    portal: undefined,
    portalPath: undefined,
    workspaceRoot: undefined,
  };

  const opacityTypes = createOpacityDecorationTypes();
  const phraseTypes = createPhraseDecorationTypes();
  const debounceMs = () =>
    workspace.getConfiguration("semipy").get<number>("debounceMs") ?? 200;

  const signFlip = new SignFlipCoordinator(() =>
    workspace.getConfiguration("semipy").get<boolean>("signFlipOnSkeletonEdit") ?? true,
  );

  const linked = new LinkedHighlightCoordinator(
    () =>
      workspace.getConfiguration("semipy").get<number>("linkedHighlightFadeMs") ?? 1500,
  );

  const diag = new SemipyDiagnosticManager(() => portalState.workspaceRoot);

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
    const root = portalState.workspaceRoot;
    refreshOpacityDecorations(editor, opacityTypes.reasoningDim);
    refreshPhraseDecorations(editor, portalState.portal, root, phraseTypes);
    const n = portalState.portal ? Object.keys(portalState.portal.slots).length : 0;
    status.text = `Semipy: ${n} slot(s)`;
    status.show();
    tree.refresh();
    diag.refresh();
    linked.onSelectionOrPortal(editor, portalState.portal, root);
  }

  const opacitySub = subscribeOpacityWrapper(opacityTypes, debounceMs, refreshAllDecorations);

  context.subscriptions.push(
    treeView,
    status,
    opacitySub,
    signFlip.attach(),
    { dispose: () => linked.dispose() },
    diag,
    window.onDidChangeTextEditorSelection((e) => {
      refreshPortalForUri(e.textEditor.document.uri.fsPath, portalState);
      linked.onSelectionOrPortal(e.textEditor, portalState.portal, portalState.workspaceRoot);
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
        () => portalState.workspaceRoot,
      ),
    ),
    registerCommitTextProvider(),
    commands.registerCommand("semipy.openSplitView", async () => {
      const ed = window.activeTextEditor;
      if (!ed) {
        return;
      }
      refreshPortalForUri(ed.document.uri.fsPath, portalState);
      if (!portalState.portal || !portalState.workspaceRoot) {
        void window.showWarningMessage("Semipy: no portal for this file.");
        return;
      }
      await openDispatchSplitView(portalState.workspaceRoot, portalState.portal.module_name);
    }),
    commands.registerCommand("semipy.refreshHistory", () => {
      const ed = window.activeTextEditor;
      refreshAllDecorations(ed);
      tree.refresh();
    }),
    commands.registerCommand(
      "semipy.viewGeneratedCode",
      async (slotId: string, commitId: string) => {
        const fromDisk =
          portalState.portalPath !== undefined
            ? loadPortalJson(portalState.portalPath)
            : undefined;
        const portal = fromDisk ?? portalState.portal;
        const slot = portal?.slots[slotId];
        const src = slot?.commits[commitId]?.generated_source;
        if (!src) {
          void window.showWarningMessage("Semipy: commit source not loaded; refresh history.");
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
    const w = workspace.createFileSystemWatcher(new RelativePattern(wf, ".semiformal/**/*"));
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
    w.onDidChange(fire);
    w.onDidCreate(fire);
    w.onDidDelete(fire);
    context.subscriptions.push(w);
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

export function deactivate(): void {}
