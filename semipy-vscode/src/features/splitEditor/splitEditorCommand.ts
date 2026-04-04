import * as path from "path";
import { Uri, ViewColumn, window, workspace } from "vscode";

export async function openDispatchSplitView(
  workspaceRoot: string,
  moduleName: string,
): Promise<void> {
  const rel = path.join(".semiformal", "runtime", `${moduleName}.semi.py`);
  const abs = path.join(workspaceRoot, rel);
  const uri = Uri.file(abs);
  try {
    const doc = await workspace.openTextDocument(uri);
    await window.showTextDocument(doc, {
      viewColumn: ViewColumn.Beside,
      preserveFocus: false,
    });
  } catch {
    void window.showErrorMessage(`Semipy: could not open dispatch file: ${abs}`);
  }
}
