/**
 * Steering the *system* (as opposed to a single slot). semipy's behaviour is
 * governed by SemiConfig flags -- gates that decide how cautious it is about
 * accepting generated code and touching the real world. These live in a runtime
 * `configure(...)` call, so the extension cannot flip them on a live kernel; what
 * it can do is make the surface legible and scaffold the call for you.
 *
 * This is honest authoring assistance: pick the modes you want, and we write (or
 * copy) the matching `configure(...)` snippet.
 */
import type { TextEditor } from "vscode";
import { Position, SnippetString, env, window } from "vscode";

interface ModeFlag {
  flag: string;
  label: string;
  detail: string;
  /** true when enabling it changes real-world behaviour (shown with a caution mark). */
  caution?: boolean;
}

/** The steerable safety/behaviour surface, grouped roughly safe -> high-stakes. */
export const MODE_FLAGS: ModeFlag[] = [
  { flag: "verbose", label: "Verbose pipeline stream", detail: "Show the live generation stream and phase strip." },
  { flag: "contract_gate", label: "Contract gate", detail: "Reject generated code that violates a carried behavioral case; regenerate." },
  { flag: "contract_maintainer", label: "Contract maintainer (LLM)", detail: "Let an LLM propose golden-master examples and metamorphic relations." },
  { flag: "sketch_library_learning", label: "Pattern learning", detail: "Learn NL->code sketches so similar specs can INSTANTIATE without an LLM call." },
  { flag: "effects_enabled", label: "Effects subsystem", detail: "Treat slots that declare fx as effectful (reified real-world effects)." },
  { flag: "effect_staging", label: "Effect staging (shadow)", detail: "Run effects against a shadow of the artifact; capture compensations." },
  { flag: "effect_gate", label: "Effect gate", detail: "Enforce reversibility + bounded blast radius before an effect is allowed.", caution: true },
  { flag: "effect_smt", label: "Effect proofs", detail: "Prove bounded blast radius for all inputs via schema superkeys." },
  { flag: "effect_auto_apply", label: "Auto-apply effects", detail: "Commit verified effects to the real artifact (otherwise dry-run).", caution: true },
  { flag: "effect_require_approval_external", label: "Approve external effects", detail: "Require explicit approval before sending to an external (non-shadowable) target." },
];

export function buildConfigureSnippet(flags: string[]): string {
  if (!flags.length) {
    return "configure()";
  }
  const body = flags.map((f) => `    ${f}=True,`).join("\n");
  return `configure(\n${body}\n)`;
}

function hasConfigureImport(text: string): boolean {
  return /from\s+semipy\s+import\s+[^\n]*\bconfigure\b/.test(text) || /\bimport\s+semipy\b/.test(text);
}

/** Insert a configure() snippet after the top import block of the active editor. */
async function insertConfigure(editor: TextEditor, snippet: string): Promise<void> {
  const doc = editor.document;
  let lastImport = -1;
  for (let i = 0; i < Math.min(doc.lineCount, 200); i++) {
    const t = doc.lineAt(i).text.trim();
    if (t.startsWith("import ") || t.startsWith("from ")) {
      lastImport = i;
    } else if (t && !t.startsWith("#") && lastImport >= 0) {
      break;
    }
  }
  const needsImport = !hasConfigureImport(doc.getText());
  const importLine = needsImport ? "from semipy import configure\n" : "";
  const at = new Position(lastImport + 1, 0);
  const block = `${importLine}${snippet}\n\n`;
  await editor.insertSnippet(new SnippetString(block.replace(/\$/g, "\\$")), at);
}

export async function runSteeringModesQuickPick(): Promise<void> {
  const items = MODE_FLAGS.map((m) => ({
    label: `${m.caution ? "$(alert) " : ""}${m.label}`,
    description: m.flag,
    detail: m.detail,
    flag: m.flag,
  }));
  const picked = await window.showQuickPick(items, {
    canPickMany: true,
    title: "Semipy · Steering — choose the modes to enable",
    placeHolder: "These map to configure(...) flags. Caution-marked modes change real-world behaviour.",
  });
  if (!picked || picked.length === 0) {
    return;
  }
  const snippet = buildConfigureSnippet(picked.map((p) => p.flag));
  const action = await window.showQuickPick(
    [
      { label: "$(insert) Insert configure() at top of file", id: "insert" },
      { label: "$(clippy) Copy to clipboard", id: "copy" },
    ],
    { title: "Apply steering", placeHolder: snippet.replace(/\n\s*/g, " ") },
  );
  if (!action) {
    return;
  }
  if (action.id === "copy") {
    await env.clipboard.writeText(snippet);
    void window.showInformationMessage("Semipy: configure(...) snippet copied to clipboard.");
    return;
  }
  const editor = window.activeTextEditor;
  if (!editor) {
    await env.clipboard.writeText(snippet);
    void window.showInformationMessage("Semipy: no active editor — snippet copied to clipboard instead.");
    return;
  }
  await insertConfigure(editor, snippet);
}
