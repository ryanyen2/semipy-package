/**
 * Mirrors semipy/lowering.py hash-arrow detection for editor features.
 * Line indices are 0-based unless noted.
 */

export function isHashArrowLine(line: string): boolean {
  const stripped = line.replace(/^\s+/, "");
  return stripped.startsWith("#>") || stripped.startsWith("# >");
}

export function isReasoningLine(line: string): boolean {
  const stripped = line.replace(/^\s+/, "");
  return stripped.startsWith("#<") || stripped.startsWith("# <");
}

export function isSkeletonPlaceholderLine(line: string): boolean {
  const stripped = line.replace(/^\s+/, "");
  if (!stripped.startsWith("#")) {
    return false;
  }
  if (
    stripped.startsWith("#<") ||
    stripped.startsWith("#>") ||
    stripped.startsWith("# >") ||
    stripped.startsWith("# <")
  ) {
    return false;
  }
  return stripped.slice(1).trim() === "";
}

/** Inclusive 0-based [start, lastHashArrow] per contiguous #> block (skeleton # lines continue block). */
export function collectHashArrowBlockRanges(sourceLines: string[]): Array<[number, number]> {
  const blocks: Array<[number, number]> = [];
  const n = sourceLines.length;
  let i = 0;
  while (i < n) {
    if (!isHashArrowLine(sourceLines[i]!)) {
      i += 1;
      continue;
    }
    const start = i;
    let j = i;
    let lastHashArrow = i;
    while (j < n) {
      const line = sourceLines[j]!;
      if (isHashArrowLine(line)) {
        lastHashArrow = j;
        j += 1;
      } else if (isSkeletonPlaceholderLine(line)) {
        j += 1;
      } else {
        break;
      }
    }
    blocks.push([start, lastHashArrow]);
    i = j;
  }
  return blocks;
}

/** Column range (0-based) of `#>` or `# >` prefix on a line, or null. */
export function hashArrowPrefixRange(line: string): { start: number; end: number } | null {
  const m = line.match(/^(\s*)((?:#\s*>))/);
  if (!m || m.index === undefined) {
    return null;
  }
  const lead = m[1]!.length;
  const pref = m[2]!.length;
  return { start: lead, end: lead + pref };
}

/**
 * Spec text after `#>` anywhere on the line (standalone comment or trailing inline spec).
 * `baseCol` is the 0-based column where spec content begins (after `#>`).
 */
export function hashArrowSpecSuffixFromLine(line: string): { baseCol: number; suffix: string } | null {
  const m = /#\s*>/.exec(line);
  if (!m || m.index === undefined) {
    return null;
  }
  const baseCol = m.index + m[0].length;
  return { baseCol, suffix: line.slice(baseCol) };
}

/** Column range of `#<` or `# <` prefix, or null. */
export function reasoningPrefixRange(line: string): { start: number; end: number } | null {
  const m = line.match(/^(\s*)((?:#\s*<))/);
  if (!m || m.index === undefined) {
    return null;
  }
  const lead = m[1]!.length;
  const pref = m[2]!.length;
  return { start: lead, end: lead + pref };
}
