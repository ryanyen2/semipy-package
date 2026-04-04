import * as fs from "fs";
import * as path from "path";
import type { PortalJson } from "./types";
import { sessionIdFromFilename } from "./sessionId";

export function findWorkspaceRootContainingSemiformal(filePath: string): string | undefined {
  let dir = path.dirname(filePath);
  const { root } = path.parse(filePath);
  while (true) {
    const candidate = path.join(dir, ".semiformal");
    try {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
        return dir;
      }
    } catch {
      /* ignore */
    }
    if (dir === root) {
      break;
    }
    dir = path.dirname(dir);
  }
  return undefined;
}

/**
 * Match resolve_portal_anchor: Jupyter kernel paths anchor to cwd; SEMIPY_SESSION_SOURCE wins.
 */
export function resolvePortalAnchorSourcePath(actualFilename: string): string {
  const env = (process.env.SEMIPY_SESSION_SOURCE || "").trim();
  if (env) {
    return path.resolve(env);
  }
  const norm = actualFilename.replace(/\\/g, "/").toLowerCase();
  if (norm.includes("ipykernel")) {
    try {
      return process.cwd();
    } catch {
      return actualFilename;
    }
  }
  try {
    return path.resolve(actualFilename);
  } catch {
    return actualFilename;
  }
}

export function expectedPortalJsonPath(sourceFilePath: string): string | undefined {
  const root = findWorkspaceRootContainingSemiformal(sourceFilePath);
  if (!root) {
    return undefined;
  }
  const anchor = resolvePortalAnchorSourcePath(sourceFilePath);
  const sid = sessionIdFromFilename(anchor);
  return path.join(root, ".semiformal", `${sid}.portal.json`);
}

export function loadPortalJson(portalPath: string): PortalJson | undefined {
  try {
    const raw = fs.readFileSync(portalPath, "utf8");
    return JSON.parse(raw) as PortalJson;
  } catch {
    return undefined;
  }
}

export function normalizeFsPath(a: string, b: string): boolean {
  try {
    return path.normalize(a) === path.normalize(b);
  } catch {
    return a === b;
  }
}

/**
 * True if this portal belongs to the active editor file.
 * Matches session_id (same as semipy portal load) and/or source_file path.
 */
export function portalMatchesEditorFile(portal: PortalJson, editorFsPath: string): boolean {
  const anchor = resolvePortalAnchorSourcePath(editorFsPath);
  const sid = sessionIdFromFilename(anchor);
  if (portal.session_id && portal.session_id === sid) {
    return true;
  }
  const sf = portal.source_file || "";
  if (!sf) {
    return false;
  }
  try {
    if (path.normalize(sf) === path.normalize(editorFsPath)) {
      return true;
    }
    if (path.basename(sf) === path.basename(editorFsPath)) {
      return true;
    }
  } catch {
    return false;
  }
  return false;
}
