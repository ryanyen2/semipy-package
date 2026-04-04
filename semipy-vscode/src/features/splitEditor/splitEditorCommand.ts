import * as path from "path";
import { Uri, ViewColumn, window, workspace } from "vscode";

/** `cache_dir` from semipy (parent of `*.portal.json`). */
export async function openDispatchSplitView(
  portalCacheDir: string,
  moduleName: string,
): Promise<void> {
  const abs = path.join(portalCacheDir, "runtime", `${moduleName}.semi.py`);
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
