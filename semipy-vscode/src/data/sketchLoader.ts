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
