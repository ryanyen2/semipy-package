import type { Event, TreeDataProvider } from "vscode";
import { EventEmitter, MarkdownString, ThemeIcon, TreeItem, TreeItemCollapsibleState } from "vscode";
import type {
  BranchJson,
  CommitJson,
  LedgerEventJson,
  PortalJson,
  SlotJson,
} from "../../data/types";
import { type Guarantee, computeSlotInsight, groupGuarantees } from "../intelligence/slotInsight";
import { walkHistoryCommits } from "./walkHistory";
import {
  decisionIcon,
  eventIcon,
  eventLabel,
  formatCommitLabel,
  guaranteeIcon,
  healthIcon,
  truncateSpecPreview,
} from "./slotTreeItems";

export type SlotHistoryTreeItem =
  | { kind: "portal"; portal: PortalJson }
  | { kind: "slot"; portal: PortalJson; slot: SlotJson }
  | { kind: "contractGroup"; portal: PortalJson; slot: SlotJson }
  | { kind: "guarantee"; portal: PortalJson; slot: SlotJson; guarantee: Guarantee }
  | { kind: "ledgerGroup"; portal: PortalJson; slot: SlotJson }
  | { kind: "event"; portal: PortalJson; slot: SlotJson; event: LedgerEventJson }
  | { kind: "branch"; portal: PortalJson; slot: SlotJson; branch: BranchJson }
  | {
      kind: "commit";
      portal: PortalJson;
      slot: SlotJson;
      branchName: string;
      commit: CommitJson;
    };

function elementId(e: SlotHistoryTreeItem): string {
  switch (e.kind) {
    case "portal":
      return "portal";
    case "slot":
      return `slot:${e.slot.slot_id}`;
    case "contractGroup":
      return `cg:${e.slot.slot_id}`;
    case "guarantee":
      return `g:${e.slot.slot_id}:${e.guarantee.key}`;
    case "ledgerGroup":
      return `lg:${e.slot.slot_id}`;
    case "event":
      return `ev:${e.slot.slot_id}:${e.event.event_id}`;
    case "branch":
      return `br:${e.slot.slot_id}:${e.branch.name}`;
    case "commit":
      return `c:${e.slot.slot_id}:${e.branchName}:${e.commit.commit_id}`;
  }
}

export class SlotHistoryProvider implements TreeDataProvider<SlotHistoryTreeItem> {
  private readonly _onDidChange = new EventEmitter<SlotHistoryTreeItem | undefined>();
  readonly onDidChangeTreeData: Event<SlotHistoryTreeItem | undefined> = this._onDidChange.event;

  constructor(private getPortal: () => PortalJson | undefined) {}

  refresh(): void {
    this._onDidChange.fire(undefined);
  }

  /** A tree element for a slot id (for treeView.reveal from the Inspect action). */
  slotElement(slotId: string): SlotHistoryTreeItem | undefined {
    const portal = this.getPortal();
    const slot = portal?.slots?.[slotId];
    if (!portal || !slot) {
      return undefined;
    }
    return { kind: "slot", portal, slot };
  }

  getTreeItem(element: SlotHistoryTreeItem): TreeItem {
    const ti = this.buildTreeItem(element);
    ti.id = elementId(element);
    return ti;
  }

  private buildTreeItem(element: SlotHistoryTreeItem): TreeItem {
    if (element.kind === "portal") {
      const ti = new TreeItem(
        element.portal.source_file || element.portal.module_name || "portal",
        TreeItemCollapsibleState.Expanded,
      );
      ti.description = element.portal.module_name;
      ti.iconPath = new ThemeIcon("symbol-namespace");
      return ti;
    }
    if (element.kind === "slot") {
      const spec = element.slot.slot_spec?.spec_text || "";
      const insight = computeSlotInsight(element.slot);
      const ti = new TreeItem(
        truncateSpecPreview(spec) || element.slot.slot_id.slice(0, 8),
        TreeItemCollapsibleState.Expanded,
      );
      ti.description = insight
        ? `${insight.glyph} ${insight.decision} · ${insight.commitShort}`
        : element.slot.slot_id.slice(0, 8);
      ti.iconPath = insight ? healthIcon(insight.health) : new ThemeIcon("circle-outline");
      ti.contextValue = "semipy.slot";
      ti.command = {
        command: "semipy.viewActiveCode",
        title: "View active implementation",
        arguments: [element.slot.slot_id],
      };
      return ti;
    }
    if (element.kind === "contractGroup") {
      const c = computeSlotInsight(element.slot)?.contract;
      const ti = new TreeItem("Guarantees", TreeItemCollapsibleState.Collapsed);
      ti.iconPath = new ThemeIcon("law");
      if (c) {
        const patterns = new Set(
          (Object.values(element.slot.contract?.cases || {}) as { input_fingerprint?: string }[])
            .map((x) => x.input_fingerprint || ""),
        ).size;
        const bits = [`${c.distinct} distinct`];
        if (patterns > 1) bits.push(`${patterns} patterns`);
        if (c.quarantined) bits.push(`${c.quarantined} quarantined`);
        ti.description = bits.join(" · ");
      }
      return ti;
    }
    if (element.kind === "guarantee") {
      const g = element.guarantee;
      const quarantined = g.patterns === 0 && g.quarantined > 0;
      const ti = new TreeItem(g.label, TreeItemCollapsibleState.None);
      ti.iconPath = guaranteeIcon(g);
      ti.description = g.patterns > 1 ? `× ${g.patterns} patterns` : quarantined ? "quarantined" : g.kind;
      const sample = g.sampleRepr ? `\n\nExample input: \`${g.sampleRepr}\`` : "";
      ti.tooltip = new MarkdownString(
        `**\`${g.label}\`** _(${g.kind})_\n\n${g.meaning}${g.reason ? `\n\n${g.reason}` : ""}${sample}`,
      );
      ti.contextValue = quarantined ? "semipy.guaranteeQuarantined" : "semipy.guarantee";
      return ti;
    }
    if (element.kind === "ledgerGroup") {
      const e = computeSlotInsight(element.slot)?.effect;
      const ti = new TreeItem("Effects", TreeItemCollapsibleState.Collapsed);
      ti.iconPath = new ThemeIcon("zap");
      if (e) {
        const bits = [`${e.applied} applied`];
        if (e.reverted) bits.push(`${e.reverted} reverted`);
        if (e.pending) bits.push(`${e.pending} pending`);
        ti.description = bits.join(" · ");
      }
      return ti;
    }
    if (element.kind === "event") {
      const e = element.event;
      const ti = new TreeItem(eventLabel(e), TreeItemCollapsibleState.None);
      ti.iconPath = eventIcon(e);
      ti.description = e.status || "applied";
      ti.contextValue =
        (e.status || "applied") === "applied" ? "semipy.ledgerEvent" : "semipy.ledgerEventReverted";
      ti.command = {
        command: "semipy.viewActiveCode",
        title: "View active implementation",
        arguments: [element.slot.slot_id],
      };
      return ti;
    }
    if (element.kind === "branch") {
      const isDefault = element.branch.name === (element.slot.default_branch || "main");
      const ti = new TreeItem(
        `${element.branch.name}${isDefault ? " (HEAD)" : ""}`,
        TreeItemCollapsibleState.Collapsed,
      );
      ti.iconPath = new ThemeIcon("git-branch");
      return ti;
    }
    const ti = new TreeItem(formatCommitLabel(element.commit), TreeItemCollapsibleState.None);
    ti.iconPath = decisionIcon(element.commit.decision);
    ti.contextValue = "semipy.commit";
    ti.command = {
      command: "semipy.viewGeneratedCode",
      title: "View generated code",
      arguments: [element.slot.slot_id, element.commit.commit_id],
    };
    return ti;
  }

  getChildren(element?: SlotHistoryTreeItem): SlotHistoryTreeItem[] {
    const portal = this.getPortal();
    if (!portal) {
      return [];
    }
    if (!element) {
      return [{ kind: "portal", portal }];
    }
    if (element.kind === "portal") {
      return Object.values(element.portal.slots)
        .filter((slot) => slot.commits && Object.keys(slot.commits).length > 0)
        .map((slot) => ({ kind: "slot" as const, portal: element.portal, slot }));
    }
    if (element.kind === "slot") {
      const out: SlotHistoryTreeItem[] = [];
      const hasContract =
        !!element.slot.contract?.cases && Object.keys(element.slot.contract.cases).length > 0;
      const hasLedger = !!element.slot.ledger?.events && element.slot.ledger.events.length > 0;
      if (hasContract) {
        out.push({ kind: "contractGroup", portal: element.portal, slot: element.slot });
      }
      if (hasLedger) {
        out.push({ kind: "ledgerGroup", portal: element.portal, slot: element.slot });
      }
      for (const branch of Object.values(element.slot.branches)) {
        out.push({ kind: "branch", portal: element.portal, slot: element.slot, branch });
      }
      return out;
    }
    if (element.kind === "contractGroup") {
      const cases = Object.values(element.slot.contract?.cases || {});
      return groupGuarantees(cases)
        .filter((g) => g.patterns > 0 || g.quarantined > 0)
        .map((guarantee) => ({
          kind: "guarantee" as const,
          portal: element.portal,
          slot: element.slot,
          guarantee,
        }));
    }
    if (element.kind === "ledgerGroup") {
      const events = element.slot.ledger?.events || [];
      return [...events].reverse().map((event) => ({
        kind: "event" as const,
        portal: element.portal,
        slot: element.slot,
        event,
      }));
    }
    if (element.kind === "branch") {
      const chain = walkHistoryCommits(element.slot, element.branch.head);
      return chain.map((commit) => ({
        kind: "commit" as const,
        portal: element.portal,
        slot: element.slot,
        branchName: element.branch.name,
        commit,
      }));
    }
    return [];
  }

  getParent(element: SlotHistoryTreeItem): SlotHistoryTreeItem | undefined {
    const portal = element.kind === "portal" ? undefined : element.portal;
    if (!portal) {
      return undefined;
    }
    switch (element.kind) {
      case "slot":
        return { kind: "portal", portal };
      case "contractGroup":
      case "ledgerGroup":
      case "branch":
        return { kind: "slot", portal, slot: element.slot };
      case "guarantee":
        return { kind: "contractGroup", portal, slot: element.slot };
      case "event":
        return { kind: "ledgerGroup", portal, slot: element.slot };
      case "commit":
        return {
          kind: "branch",
          portal,
          slot: element.slot,
          branch: element.slot.branches[element.branchName] || {
            name: element.branchName,
            head: element.commit.commit_id,
          },
        };
      default:
        return undefined;
    }
  }
}
