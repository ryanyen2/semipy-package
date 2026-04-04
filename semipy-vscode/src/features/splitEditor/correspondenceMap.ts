import * as path from "path";
import type { PortalJson, SlotJson } from "../../data/types";
import { parseSpecMapEntry } from "../../data/dispatchLoader";
import { pathsEqualRobust } from "../../data/portalLoader";

export function pathsEqual(a: string, b: string): boolean {
  return pathsEqualRobust(a, b);
}

/** 1-based line in source file -> slot covering that line, if any. Uses current buffer text when possible. */
export function findSlotForSourceLine(
  portal: PortalJson,
  sourceFsPath: string,
  line1: number,
  fullText: string,
): SlotJson | undefined {
  for (const slot of Object.values(portal.slots)) {
    const sp = slot.slot_spec;
    const span = sp?.source_span;
    if (!span || span.length < 3) {
      continue;
    }
    const [fn] = span;
    if (!pathsEqualRobust(fn, sourceFsPath)) {
      continue;
    }
    const block = resolveSourceBlockRange(fullText, slot);
    if (!block) {
      continue;
    }
    if (line1 >= block.startLine1 && line1 <= block.endLine1) {
      return slot;
    }
  }
  return undefined;
}

/** If portal span text does not match file, search for spec_text substring (handles line drift). */
export function resolveSourceBlockRange(
  fullText: string,
  slot: SlotJson,
): { startLine1: number; endLine1: number } | undefined {
  const sp = slot.slot_spec;
  if (!sp?.source_span || sp.source_span.length < 3) {
    return undefined;
  }
  const [, start, end] = sp.source_span;
  const lines = fullText.split(/\r?\n/);
  const slice = lines.slice(start - 1, end).join("\n");
  const specText = (sp.spec_text || "").trim();
  if (specText && slice.trim() === specText) {
    return { startLine1: start, endLine1: end };
  }
  if (specText) {
    const idx = fullText.indexOf(specText);
    if (idx >= 0) {
      const before = fullText.slice(0, idx);
      const startLine1 = before.split(/\r?\n/).length;
      const spanLines = specText.split(/\r?\n/).length;
      return { startLine1, endLine1: startLine1 + spanLines - 1 };
    }
  }
  return { startLine1: start, endLine1: end };
}

/** semipy `cache_dir` (directory containing `*.portal.json`). */
export function dispatchRangeForSlot(
  portal: PortalJson,
  slotId: string,
  portalCacheDir: string,
): { uriPath: string; startLine1: number; endLine1: number } | undefined {
  const raw = portal.spec_map[slotId];
  if (!raw) {
    return undefined;
  }
  const parsed = parseSpecMapEntry(raw);
  if (!parsed) {
    return undefined;
  }
  const mod = portal.module_name || "unknown";
  const runtimePath = path.join(portalCacheDir, "runtime", `${mod}.semi.py`);
  return {
    uriPath: runtimePath,
    startLine1: parsed.startLine,
    endLine1: parsed.endLine,
  };
}
