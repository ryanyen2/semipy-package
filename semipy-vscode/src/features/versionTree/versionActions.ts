import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";
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

function expandWorkspaceVars(s: string): string {
  let out = s;
  const folders = workspace.workspaceFolders ?? [];
  if (out.includes("${workspaceFolder}")) {
    const first = folders[0]?.uri.fsPath;
    if (first) {
      out = out.replace(/\$\{workspaceFolder\}/g, first);
    }
  }
  return out;
}

function resolveConfiguredPythonPath(raw: string): string {
  let out = expandWorkspaceVars(raw.trim());
  if (!out) {
    return out;
  }
  if (!path.isAbsolute(out)) {
    const wf = workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (wf) {
      out = path.resolve(wf, out);
    }
  }
  try {
    return path.normalize(out);
  } catch {
    return out;
  }
}

function pushIfFile(out: string[], p: string): void {
  try {
    if (fs.existsSync(p) && fs.statSync(p).isFile()) {
      out.push(p);
    }
  } catch {
    /* ignore */
  }
}

/** Prefer repo `.venv` / `venv` next to workspace roots and one level up (e.g. `examples/` with venv at repo root). */
function candidateVenvInterpreterPaths(): string[] {
  const candidates: string[] = [];
  const roots = new Set<string>();
  for (const wf of workspace.workspaceFolders ?? []) {
    roots.add(wf.uri.fsPath);
    roots.add(path.dirname(wf.uri.fsPath));
  }
  for (const root of roots) {
    pushIfFile(candidates, path.join(root, ".venv", "bin", "python"));
    pushIfFile(candidates, path.join(root, ".venv", "bin", "python3"));
    pushIfFile(candidates, path.join(root, "venv", "bin", "python"));
    pushIfFile(candidates, path.join(root, "venv", "bin", "python3"));
    pushIfFile(candidates, path.join(root, ".venv", "Scripts", "python.exe"));
    pushIfFile(candidates, path.join(root, "venv", "Scripts", "python.exe"));
  }
  return candidates;
}

/**
 * Prefer semipy.pythonPath, then any workspace `.venv`/`venv` on disk, then the Python extension
 * interpreter, then python3. (Avoids relying on the shell PATH when it points at conda without semipy.)
 */
export function resolvePythonExecutable(): string {
  const cfg = workspace.getConfiguration("semipy");
  const explicit = resolveConfiguredPythonPath((cfg.get<string>("pythonPath") || "").trim());
  if (explicit) {
    return explicit;
  }
  for (const p of candidateVenvInterpreterPaths()) {
    return p;
  }
  const py = workspace.getConfiguration("python");
  const interpreter = expandWorkspaceVars((py.get<string>("defaultInterpreterPath") || "").trim());
  if (interpreter) {
    return interpreter;
  }
  return process.platform === "win32" ? "python" : "python3";
}

export function runSemipyCli(
  args: string[],
  cwd: string,
): Promise<{ stdout: string; stderr: string; code: number | null }> {
  return new Promise((resolve) => {
    const exe = resolvePythonExecutable();
    execFile(exe, ["-m", "semipy", ...args], { cwd }, (error, stdout, stderr) => {
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
