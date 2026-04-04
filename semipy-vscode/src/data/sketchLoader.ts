import * as fs from "fs";
import * as path from "path";
import type { SemanticBindingJson, SketchLibraryJson } from "./types";

export function sketchLibraryPath(semiformalRoot: string): string {
  return path.join(semiformalRoot, ".semiformal", "sketch_library.json");
}

export function loadSketchLibrary(semiformalRoot: string): SketchLibraryJson | undefined {
  const p = sketchLibraryPath(semiformalRoot);
  try {
    const raw = fs.readFileSync(p, "utf8");
    return JSON.parse(raw) as SketchLibraryJson;
  } catch {
    return undefined;
  }
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
