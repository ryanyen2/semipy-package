import { execFile } from "child_process";
import type { Disposable } from "vscode";
import { Uri, window, workspace } from "vscode";

const previewSources = new Map<string, string>();

export function setCommitPreviewSource(slotId: string, commitId: string, source: string): Uri {
  const key = `${slotId}:${commitId}`;
  previewSources.set(key, source);
  return Uri.from({ scheme: "semipy-commit", path: "/preview.py", query: key });
}

export function registerCommitTextProvider(): Disposable {
  return workspace.registerTextDocumentContentProvider("semipy-commit", {
    provideTextDocumentContent(uri: Uri): string {
      const key = uri.query;
      return previewSources.get(key) || "# Preview expired; run the tree command again.\n";
    },
  });
}

export async function viewGeneratedCode(
  slotId: string,
  commitId: string,
  source: string,
): Promise<void> {
  const uri = setCommitPreviewSource(slotId, commitId, source);
  const doc = await workspace.openTextDocument(uri);
  await window.showTextDocument(doc, { preview: true });
}

export function runSemipyCli(
  args: string[],
  cwd: string,
): Promise<{ stdout: string; stderr: string; code: number | null }> {
  return new Promise((resolve) => {
    execFile("python3", ["-m", "semipy", ...args], { cwd }, (error, stdout, stderr) => {
      let code = 0;
      if (error) {
        const c = (error as NodeJS.ErrnoException).code;
        code = typeof c === "number" ? c : 1;
      }
      resolve({
        stdout: String(stdout),
        stderr: String(stderr),
        code,
      });
    });
  });
}
