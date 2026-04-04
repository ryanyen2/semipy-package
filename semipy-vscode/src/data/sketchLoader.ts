import * as fs from "fs";
import * as path from "path";
import type { SemanticBindingJson, SketchLibraryJson } from "./types";

/** `cache_dir` from semipy (portal json directory); sketch library lives next to `*.portal.json`. */
export function sketchLibraryPath(cacheDir: string): string {
  return path.join(cacheDir, "sketch_library.json");
}

function loadSketchLibraryFile(p: string): SketchLibraryJson | undefined {
  try {
    const raw = fs.readFileSync(p, "utf8");
    return JSON.parse(raw) as SketchLibraryJson;
  } catch {
    return undefined;
  }
}

/**
 * Merge `sketch_library.json` from the portal cache dir and from every `.semiformal*` directory
 * under each workspace root (plus one non-hidden subdirectory), so bindings from pattern learning
 * are visible even when the active portal uses a different cache folder name.
 */
export function loadSketchLibraryMerged(
  cacheDir: string,
  workspaceRoots: readonly string[] | undefined,
): SketchLibraryJson | undefined {
  const merged: SketchLibraryJson = { version: 1, sketches: {}, bindings: {} };
  let any = false;

  const absorb = (lib: SketchLibraryJson | undefined) => {
    if (!lib) {
      return;
    }
    any = true;
    Object.assign(merged.bindings ||= {}, lib.bindings || {});
    Object.assign(merged.sketches ||= {}, lib.sketches || {});
    if (lib.version !== undefined) {
      merged.version = lib.version;
    }
  };

  absorb(loadSketchLibraryFile(sketchLibraryPath(cacheDir)));

  const scanDir = (dir: string) => {
    let names: string[];
    try {
      names = fs.readdirSync(dir);
    } catch {
      return;
    }
    for (const name of names) {
      if (!name.startsWith(".semiformal")) {
        continue;
      }
      const sub = path.join(dir, name);
      let isDir = false;
      try {
        isDir = fs.statSync(sub).isDirectory();
      } catch {
        continue;
      }
      if (!isDir) {
        continue;
      }
      absorb(loadSketchLibraryFile(path.join(sub, "sketch_library.json")));
    }
  };

  for (const root of workspaceRoots ?? []) {
    scanDir(root);
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(root, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const ent of entries) {
      if (!ent.isDirectory() || ent.name.startsWith(".")) {
        continue;
      }
      scanDir(path.join(root, ent.name));
    }
  }

  return any ? merged : undefined;
}

export function loadSketchLibrary(cacheDir: string): SketchLibraryJson | undefined {
  return loadSketchLibraryMerged(cacheDir, undefined);
}

export function bindingById(
  lib: SketchLibraryJson | undefined,
  bindingId: string,
): SemanticBindingJson | undefined {
  if (!lib?.bindings || !bindingId) {
    return undefined;
  }
  return lib.bindings[bindingId];
}

/**
 * Prefer `commit.binding_id`; if missing, find a sketch whose `source_commit_ids` contains this
 * commit and use its `binding_id` (matches sketch_library.json when async portal patch did not run,
 * e.g. demo `_sync_sketch_from_function` historically only saved the library file).
 */
export function resolveBindingIdForCommit(
  lib: SketchLibraryJson | undefined,
  commitId: string,
  explicitBindingId: string | undefined,
): string | undefined {
  const e = (explicitBindingId || "").trim();
  if (e) {
    return e;
  }
  if (!lib?.sketches || !commitId) {
    return undefined;
  }
  for (const sk of Object.values(lib.sketches)) {
    if (!sk || typeof sk !== "object") {
      continue;
    }
    const ids = sk.source_commit_ids || [];
    if (!ids.includes(commitId)) {
      continue;
    }
    const bid = (sk.binding_id || "").trim();
    if (bid) {
      return bid;
    }
  }
  return undefined;
}
