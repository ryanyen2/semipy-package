/**
 * The #< reasoning surface, made steerable.
 *
 * semipy writes #< lines that explain *why* it generated what it did, split into
 * two zones: PROVENANCE (intent / given / by / unless) above the slot anchor, and
 * EFFECT (yields / verified) below it. These are inferred notes -- kept
 * dim (opacity = ephemerality). The user steers the slot by *promoting* a note to
 * a #> contract line: that edits spec_text, so the next resolution honours it.
 *
 * This module makes that promotion discoverable (a lightbulb code-action + a hover)
 * instead of relying on the hidden "edit the char" sign-flip, and classifies each
 * key's zone so the syntax painter can tint it.
 */
import type {
  CancellationToken,
  CodeAction,
  CodeActionContext,
  CodeActionProvider,
  Hover,
  HoverProvider,
  Position,
  Range,
  TextDocument,
} from "vscode";
import {
  CodeAction as VsCodeAction,
  CodeActionKind,
  Hover as VsHover,
  MarkdownString,
} from "vscode";

export type SteeringZone = "provenance" | "effect";

const PROVENANCE_KEYS = new Set(["intent", "given", "by", "unless"]);
const EFFECT_KEYS = new Set(["yields", "verified"]);

const KEY_HELP: Record<string, string> = {
  intent: "what this slot is meant to achieve",
  given: "the inputs / assumptions it was generated under",
  by: "how semipy chose to implement it (the approach taken)",
  unless: "an edge case or exception the implementation guards against",
  yields: "the shape of the value it returns",
  verified: "what was checked to hold (derived, not synthesised)",
};

export interface ParsedSteering {
  key: string;
  zone: SteeringZone;
  /** 0-based column range of the `key` token within the line. */
  keyStart: number;
  keyEnd: number;
}

/** Parse a `#< key: ...` line into its steering key + zone, or null. */
export function parseSteeringLine(lineText: string): ParsedSteering | null {
  const m = lineText.match(/^(\s*)#\s*<\s*([a-zA-Z_]+)\s*:/);
  if (!m) {
    return null;
  }
  const key = m[2]!.toLowerCase();
  let zone: SteeringZone | undefined;
  if (PROVENANCE_KEYS.has(key)) {
    zone = "provenance";
  } else if (EFFECT_KEYS.has(key)) {
    zone = "effect";
  }
  if (!zone) {
    return null;
  }
  const keyStart = m.index! + m[1]!.length + lineText.slice(m[1]!.length).indexOf(m[2]!);
  return { key, zone, keyStart, keyEnd: keyStart + m[2]!.length };
}

function isReasoning(lineText: string): boolean {
  const s = lineText.replace(/^\s+/, "");
  return s.startsWith("#<") || s.startsWith("# <");
}

/** Code actions on #< lines: promote to a #> contract, or dismiss the note. */
export function createSteeringCodeActionProvider(): CodeActionProvider {
  return {
    provideCodeActions(
      document: TextDocument,
      range: Range,
      _context: CodeActionContext,
      _token: CancellationToken,
    ): CodeAction[] {
      const line = document.lineAt(range.start.line);
      if (!isReasoning(line.text)) {
        return [];
      }
      const parsed = parseSteeringLine(line.text);
      const keyLabel = parsed ? ` (${parsed.key})` : "";
      const pin = new VsCodeAction(
        `Semipy: Pin as contract${keyLabel} → #>`,
        CodeActionKind.RefactorRewrite,
      );
      pin.command = {
        command: "semipy.promoteReasoningLine",
        title: "Pin as contract",
        arguments: [document.uri, range.start.line],
      };
      const dismiss = new VsCodeAction("Semipy: Dismiss this note", CodeActionKind.RefactorRewrite);
      dismiss.command = {
        command: "semipy.dismissReasoningLine",
        title: "Dismiss note",
        arguments: [document.uri, range.start.line],
      };
      return [pin, dismiss];
    },
  };
}

/** Hover on #< lines explaining the key, its zone, and the promote action. */
export function createSteeringHoverProvider(): HoverProvider {
  return {
    provideHover(document: TextDocument, position: Position, _token: CancellationToken): Hover | undefined {
      const line = document.lineAt(position.line);
      if (!isReasoning(line.text)) {
        return undefined;
      }
      const parsed = parseSteeringLine(line.text);
      const md = new MarkdownString();
      md.isTrusted = true;
      md.supportThemeIcons = true;
      if (parsed) {
        const zoneLabel = parsed.zone === "provenance" ? "provenance" : "effect";
        md.appendMarkdown(
          `$(lightbulb) **\`${parsed.key}\`** — ${KEY_HELP[parsed.key] || "an inferred note"}  ·  *${zoneLabel}*\n\n`,
        );
      } else {
        md.appendMarkdown(`$(lightbulb) **Inferred note** — semipy's reasoning, not part of your contract.\n\n`);
      }
      const q = encodeURIComponent(JSON.stringify([document.uri.toString(), position.line]));
      md.appendMarkdown(
        `[$(pin) Pin as contract (#>)](command:semipy.promoteReasoningLine?${q})  ·  ` +
          `[$(close) Dismiss](command:semipy.dismissReasoningLine?${q})`,
      );
      return new VsHover(md);
    },
  };
}
