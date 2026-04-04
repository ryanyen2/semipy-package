import * as crypto from "crypto";

/**
 * Byte-identical to semipy.types.session_id_from_filename (SHA256 of basename, lowercased, .py stripped, [:16]).
 */
export function sessionIdFromFilename(filename: string): string {
  if (!filename || filename === "<unknown>") {
    return crypto.createHash("sha256").update("<unknown>").digest("hex").slice(0, 16);
  }
  let normalized = filename.replace(/\\/g, "/").trim().toLowerCase();
  let base =
    normalized.includes("/") ? normalized.split("/").pop() ?? normalized : normalized;
  if (base.endsWith(".py")) {
    base = base.slice(0, -3);
  }
  if (!base) {
    base = normalized;
  }
  return crypto.createHash("sha256").update(base).digest("hex").slice(0, 16);
}

/** Mirrors semipy.types.session_module_name_from_filename (no lowercasing). */
export function sessionModuleNameFromFilename(filename: string): string {
  if (!filename || filename === "<unknown>") {
    return "unknown";
  }
  const normalized = filename.replace(/\\/g, "/").trim();
  let base =
    normalized.includes("/") ? normalized.split("/").pop() ?? normalized : normalized;
  if (base.endsWith(".py")) {
    base = base.slice(0, -3);
  }
  return base || "unknown";
}
