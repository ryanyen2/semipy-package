import * as fs from "fs";
import * as path from "path";
import type { Diagnostic as VSDiagnostic } from "vscode";
import {
  Diagnostic,
  DiagnosticSeverity,
  languages,
  Uri,
  Range,
  Position,
} from "vscode";
import type { DiagnosticEntryJson, DiagnosticsFileJson } from "../../data/types";

export class SemipyDiagnosticManager {
  private collection = languages.createDiagnosticCollection("semipy");

  constructor(private readonly semiformalRoot: () => string | undefined) {}

  dispose(): void {
    this.collection.dispose();
  }

  refresh(): void {
    this.collection.clear();
    const root = this.semiformalRoot();
    if (!root) {
      return;
    }
    const p = path.join(root, ".semiformal", "diagnostics.json");
    let data: DiagnosticsFileJson;
    try {
      const raw = fs.readFileSync(p, "utf8");
      data = JSON.parse(raw) as DiagnosticsFileJson;
    } catch {
      return;
    }
    const byFile = new Map<string, VSDiagnostic[]>();
    for (const e of data.entries || []) {
      const d = this.entryToDiagnostic(e);
      if (!d) {
        continue;
      }
      const fp = e.source_file;
      const list = byFile.get(fp) || [];
      list.push(d);
      byFile.set(fp, list);
    }
    for (const [fp, diags] of byFile) {
      const uri = Uri.file(fp);
      this.collection.set(uri, diags);
    }
  }

  private entryToDiagnostic(e: DiagnosticEntryJson): VSDiagnostic | undefined {
    const start = Math.max(1, e.source_line_start) - 1;
    const end = Math.max(1, e.source_line_end) - 1;
    const sev =
      e.severity === "error"
        ? DiagnosticSeverity.Error
        : e.severity === "warning"
          ? DiagnosticSeverity.Warning
          : DiagnosticSeverity.Information;
    const d = new Diagnostic(
      new Range(new Position(start, 0), new Position(end, 2000)),
      e.message,
      sev,
    );
    d.source = "semipy";
    d.code = e.slot_id ? `semi-call-error:${e.slot_id}` : e.code || "semi-call-error";
    d.relatedInformation = [];
    if (e.generated_path && e.generated_line_range?.length === 2) {
      const [a, b] = e.generated_line_range;
      const root = this.semiformalRoot() || "";
      const gp = path.isAbsolute(e.generated_path)
        ? e.generated_path
        : path.join(root, e.generated_path);
      d.relatedInformation!.push({
        location: {
          uri: Uri.file(gp),
          range: new Range(
            new Position(Math.max(1, a) - 1, 0),
            new Position(Math.max(1, b) - 1, 2000),
          ),
        },
        message: "Generated implementation",
      });
    }
    return d;
  }
}
