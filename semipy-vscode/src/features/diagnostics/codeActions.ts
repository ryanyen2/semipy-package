import type { CodeActionProvider, Diagnostic as VSDiagnostic } from "vscode";
import { CodeAction, CodeActionKind } from "vscode";

function slotIdFromDiagnosticCode(code: string | number | { value: string | number } | undefined): string | undefined {
  const s = typeof code === "object" && code !== null ? String(code.value) : String(code ?? "");
  if (s.startsWith("semi-call-error:")) {
    return s.slice("semi-call-error:".length);
  }
  return undefined;
}

export function createRegenerateCodeActionProvider(
  getWorkspaceRoot: () => string | undefined,
  getPortalRelPath: () => string | undefined,
): CodeActionProvider {
  return {
    provideCodeActions(_document, _range, context): CodeAction[] {
      const ws = getWorkspaceRoot();
      const portal = getPortalRelPath();
      if (!ws || !portal) {
        return [];
      }
      const hit = context.diagnostics.filter((d: VSDiagnostic) =>
        String(d.source || "").includes("semipy"),
      );
      if (!hit.length) {
        return [];
      }
      const slotId = slotIdFromDiagnosticCode(hit[0]!.code);
      if (!slotId) {
        return [];
      }
      const action = new CodeAction("Regenerate this spec (semipy CLI)", CodeActionKind.QuickFix);
      action.command = {
        command: "semipy.regenerateSlotDiagnostic",
        title: "Regenerate",
        arguments: [ws, portal, slotId],
      };
      return [action];
    },
  };
}
