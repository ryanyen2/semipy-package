import type { FileSystemWatcher } from "vscode";
import { Uri } from "vscode";
import { RelativePattern, workspace } from "vscode";

export function watchSemiformalFolder(
  workspaceRoot: string,
  debounceMs: number,
  onChange: () => void,
): FileSystemWatcher {
  const pattern = new RelativePattern(workspaceRoot, ".semiformal/**/*");
  const w = workspace.createFileSystemWatcher(pattern, false, false, false);
  let t: ReturnType<typeof setTimeout> | undefined;
  const fire = () => {
    if (t) {
      clearTimeout(t);
    }
    t = setTimeout(() => {
      t = undefined;
      onChange();
    }, debounceMs);
  };
  w.onDidChange(fire);
  w.onDidCreate(fire);
  w.onDidDelete(fire);
  return w;
}

export function semiformalGlobUri(workspaceFolder: Uri): Uri {
  return Uri.joinPath(workspaceFolder, ".semiformal");
}
