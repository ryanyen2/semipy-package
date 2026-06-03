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

/**
 * Byte-identical to semipy.types.session_id_for_project: SHA256 of the project-root
 * directory path (forward slashes, no trailing slash, lowercased), first 16 hex chars.
 * One portal per project (the folder rooted at the nearest `.semiformal/`).
 * `projectRoot` should be an absolute path (e.g. the parent of a `.semiformal` dir).
 *
 * This is a best-effort FAST-PATH only: unlike the Python side it does not resolve
 * symlinks (no `realpath`), so on a symlinked path the hash may differ. Portal
 * discovery does not depend on it -- the full-scan fallback in portalLoader matches
 * a project portal by its slots' source spans regardless of session_id.
 */
export function sessionIdForProject(projectRoot: string): string {
  const norm = projectRoot.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  return crypto.createHash("sha256").update(norm || "<unknown>").digest("hex").slice(0, 16);
}

/** Mirrors semipy.types.module_name_for_project (sanitized folder name, fallback "project"). */
export function moduleNameForProject(projectRoot: string): string {
  const name = projectRoot.replace(/\\/g, "/").replace(/\/+$/, "").split("/").pop() ?? "";
  const sanitized = name.replace(/[^0-9a-zA-Z_]/g, "_");
  if (!sanitized) {
    return "project";
  }
  return /^[0-9]/.test(sanitized) ? `_${sanitized}` : sanitized;
}
