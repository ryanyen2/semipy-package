/** Parse portal.spec_map entry: "function_name:start_line-end_line" (1-based inclusive). */
export function parseSpecMapEntry(entry: string): {
  fn: string;
  startLine: number;
  endLine: number;
} | undefined {
  const idx = entry.indexOf(":");
  if (idx <= 0) {
    return undefined;
  }
  const fn = entry.slice(0, idx);
  const rest = entry.slice(idx + 1);
  const m = rest.match(/^(\d+)-(\d+)$/);
  if (!m) {
    return undefined;
  }
  return {
    fn,
    startLine: parseInt(m[1]!, 10),
    endLine: parseInt(m[2]!, 10),
  };
}
