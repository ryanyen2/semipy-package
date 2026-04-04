import type { Event, TreeDataProvider } from "vscode";
import { EventEmitter, TreeItem, TreeItemCollapsibleState } from "vscode";
import type { PortalJson, SlotJson, CommitJson, BranchJson } from "../../data/types";
import { walkHistoryCommits } from "./walkHistory";
import { decisionIcon, formatCommitLabel, truncateSpecPreview } from "./slotTreeItems";

export type SlotHistoryTreeItem =
  | { kind: "portal"; portal: PortalJson }
  | { kind: "slot"; portal: PortalJson; slot: SlotJson }
  | { kind: "branch"; portal: PortalJson; slot: SlotJson; branch: BranchJson }
  | {
      kind: "commit";
      portal: PortalJson;
      slot: SlotJson;
      branchName: string;
      commit: CommitJson;
    };

export class SlotHistoryProvider implements TreeDataProvider<SlotHistoryTreeItem> {
  private readonly _onDidChange = new EventEmitter<SlotHistoryTreeItem | undefined>();
  readonly onDidChangeTreeData: Event<SlotHistoryTreeItem | undefined> = this._onDidChange.event;

  constructor(private getPortal: () => PortalJson | undefined) {}

  refresh(): void {
    this._onDidChange.fire(undefined);
  }

  getTreeItem(element: SlotHistoryTreeItem): TreeItem {
    if (element.kind === "portal") {
      const ti = new TreeItem(
        element.portal.source_file || element.portal.module_name || "portal",
        TreeItemCollapsibleState.Expanded,
      );
      ti.description = element.portal.module_name;
      return ti;
    }
    if (element.kind === "slot") {
      const spec = element.slot.slot_spec?.spec_text || "";
      const ti = new TreeItem(
        truncateSpecPreview(spec) || element.slot.slot_id.slice(0, 8),
        TreeItemCollapsibleState.Expanded,
      );
      ti.description = element.slot.slot_id.slice(0, 8);
      return ti;
    }
    if (element.kind === "branch") {
      const isDefault = element.branch.name === (element.slot.default_branch || "main");
      const ti = new TreeItem(
        `${element.branch.name}${isDefault ? " (HEAD)" : ""}`,
        TreeItemCollapsibleState.Expanded,
      );
      return ti;
    }
    const ti = new TreeItem(
      formatCommitLabel(element.commit),
      TreeItemCollapsibleState.None,
    );
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
      return Object.values(element.portal.slots).map((slot) => ({
        kind: "slot" as const,
        portal: element.portal,
        slot,
      }));
    }
    if (element.kind === "slot") {
      return Object.values(element.slot.branches).map((branch) => ({
        kind: "branch" as const,
        portal: element.portal,
        slot: element.slot,
        branch,
      }));
    }
    if (element.kind === "branch") {
      const headId = element.branch.head;
      const chain = walkHistoryCommits(element.slot, headId);
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
    return undefined;
  }
}
