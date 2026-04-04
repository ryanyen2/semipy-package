import * as fs from "fs";
import * as path from "path";
import type { PortalJson } from "./types";
import { sessionIdFromFilename } from "./sessionId";

/** Same file on disk; handles macOS case and optional realpath match. */
export function pathsEqualRobust(a: string, b: string): boolean {
  try {
    const na = path.normalize(a);
    const nb = path.normalize(b);
    if (na === nb) {
      return true;
    }
    if (na.toLowerCase() === nb.toLowerCase()) {
      return true;
    }
    try {
      const ra = fs.realpathSync(na);
      const rb = fs.realpathSync(nb);
      if (ra === rb || ra.toLowerCase() === rb.toLowerCase()) {
        return true;
      }
    } catch {
      /* ignore */
    }
  } catch {
    return false;
  }
  return false;
}

/** Portal lists this editor as a slot/enclosing file (authoritative vs session_id). */
export function portalReferencesEditorPath(portal: PortalJson, editorFsPath: string): boolean {
  try {
    for (const slot of Object.values(portal.slots)) {
      const spec = slot.slot_spec;
      if (!spec || typeof spec !== "object") {
        continue;
      }
      const src = spec.source_span;
      if (Array.isArray(src) && typeof src[0] === "string") {
        if (pathsEqualRobust(src[0], editorFsPath)) {
          return true;
        }
      }
      const enc = spec.enclosing_function_span;
      if (Array.isArray(enc) && typeof enc[0] === "string") {
        if (pathsEqualRobust(enc[0], editorFsPath)) {
          return true;
        }
      }
    }
  } catch {
    return false;
  }
  return false;
}

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

function isDirectory(p: string): boolean {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

/**
 * True if this portal belongs to the active editor file.
 * Portals often use a directory anchor (session_source); source_file is then that folder path.
 */
export function portalMatchesEditorFile(portal: PortalJson, editorFsPath: string): boolean {
  if (portalReferencesEditorPath(portal, editorFsPath)) {
    return true;
  }
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
    const normEditor = path.normalize(editorFsPath);
    const normSf = path.normalize(sf);
    if (pathsEqualRobust(normSf, normEditor)) {
      return true;
    }
    if (path.basename(normSf).toLowerCase() === path.basename(normEditor).toLowerCase()) {
      return true;
    }
    if (isDirectory(normSf)) {
      const withSep = normSf.endsWith(path.sep) ? normSf : normSf + path.sep;
      if (normEditor === normSf || normEditor.startsWith(withSep)) {
        return true;
      }
    }
  } catch {
    return false;
  }
  return false;
}

/**
 * Directories that may contain `*.portal.json` (semipy `cache_dir`), walking up from the editor file.
 * Includes `dirname/...`, `dirname/.semiformal`, and `dirname/.semiformal-*` (e.g. `.semiformal-sketch-demo`).
 */
export function collectPortalCacheDirsNearSource(sourceFilePath: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const push = (p: string) => {
    try {
      const n = path.resolve(p);
      if (!seen.has(n)) {
        seen.add(n);
        out.push(n);
      }
    } catch {
      /* ignore */
    }
  };

  let dir = path.dirname(path.resolve(sourceFilePath));
  const { root } = path.parse(sourceFilePath);
  while (true) {
    try {
      const names = fs.readdirSync(dir);
      if (names.some((f) => f.endsWith(".portal.json"))) {
        push(dir);
      }
    } catch {
      /* ignore */
    }
    const legacy = path.join(dir, ".semiformal");
    try {
      if (fs.existsSync(legacy) && fs.statSync(legacy).isDirectory()) {
        push(legacy);
      }
    } catch {
      /* ignore */
    }
    try {
      for (const name of fs.readdirSync(dir)) {
        if (!name.startsWith(".semiformal") || name === ".semiformal") {
          continue;
        }
        const sub = path.join(dir, name);
        try {
          if (fs.statSync(sub).isDirectory()) {
            push(sub);
          }
        } catch {
          /* ignore */
        }
      }
    } catch {
      /* ignore */
    }
    if (dir === root) {
      break;
    }
    dir = path.dirname(dir);
  }
  return out;
}

/**
 * Resolve `cache_dir/<session_id>.portal.json` for the current file.
 * Tries: workspace sessionSource setting, SEMIPY_SESSION_SOURCE, anchor/parent/file session ids,
 * across every discovered cache directory, then scans all portals in those dirs.
 */
export function findPortalJsonPathForEditor(
  sourceFilePath: string,
  opts?: { sessionSourceFromSettings?: string },
): string | undefined {
  const resolved = path.resolve(sourceFilePath);
  const parentDir = path.dirname(resolved);
  const cacheDirs = collectPortalCacheDirsNearSource(sourceFilePath);
  if (!cacheDirs.length) {
    return undefined;
  }

  const sessionIds: string[] = [];
  const pushId = (compute: () => string) => {
    try {
      const s = compute();
      if (s && !sessionIds.includes(s)) {
        sessionIds.push(s);
      }
    } catch {
      /* ignore */
    }
  };

  const fromSettings = (opts?.sessionSourceFromSettings || "").trim();
  if (fromSettings) {
    pushId(() => sessionIdFromFilename(path.resolve(fromSettings)));
  }
  const env = (process.env.SEMIPY_SESSION_SOURCE || "").trim();
  if (env) {
    pushId(() => sessionIdFromFilename(path.resolve(env)));
  }
  pushId(() => sessionIdFromFilename(resolvePortalAnchorSourcePath(sourceFilePath)));
  pushId(() => sessionIdFromFilename(parentDir));
  pushId(() => sessionIdFromFilename(resolved));

  const candidates: string[] = [];
  const push = (p: string) => {
    if (!candidates.includes(p)) {
      candidates.push(p);
    }
  };

  for (const cacheDir of cacheDirs) {
    for (const sid of sessionIds) {
      try {
        push(path.join(cacheDir, `${sid}.portal.json`));
      } catch {
        /* ignore */
      }
    }
  }

  for (const c of candidates) {
    try {
      if (fs.existsSync(c)) {
        const raw = fs.readFileSync(c, "utf8");
        const portal = JSON.parse(raw) as PortalJson;
        if (portalMatchesEditorFile(portal, sourceFilePath)) {
          return c;
        }
      }
    } catch {
      /* try next */
    }
  }

  for (const cacheDir of cacheDirs) {
    try {
      const files = fs.readdirSync(cacheDir).filter((f) => f.endsWith(".portal.json"));
      for (const f of files) {
        const full = path.join(cacheDir, f);
        try {
          const raw = fs.readFileSync(full, "utf8");
          const portal = JSON.parse(raw) as PortalJson;
          if (portalMatchesEditorFile(portal, sourceFilePath)) {
            return full;
          }
        } catch {
          /* ignore */
        }
      }
    } catch {
      /* ignore */
    }
  }
  return undefined;
}

/** @deprecated prefer findPortalJsonPathForEditor */
export function expectedPortalJsonPath(sourceFilePath: string): string | undefined {
  return findPortalJsonPathForEditor(sourceFilePath, undefined);
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
