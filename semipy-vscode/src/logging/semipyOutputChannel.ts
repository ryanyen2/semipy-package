import { window } from "vscode";
import type { OutputChannel } from "vscode";

let channel: OutputChannel | undefined;

export function getSemipyOutputChannel(): OutputChannel {
  if (!channel) {
    channel = window.createOutputChannel("Semipy");
  }
  return channel;
}

export function appendSemipyLog(line: string): void {
  getSemipyOutputChannel().appendLine(line);
}
