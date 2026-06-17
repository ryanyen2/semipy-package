// Node harness for decisionInsight: open-decision filtering, chip formatting,
// and IO truncation. decisionInsight.ts has no `vscode` import, so it compiles
// and runs standalone.
"use strict";

const assert = require("assert");
const path = require("path");

const emitRoot = path.resolve(__dirname, "..", ".test-emit-decisions");
const di = require(path.join(emitRoot, "features", "decisions", "decisionInsight.js"));

function slotWith(decisions) {
  return { decision_set: { decisions } };
}

// Only open forks with >1 branch surface, sorted by consequence desc.
{
  const slot = slotWith([
    { decision_id: "a", axis_label: "low", status: "open", consequence: 1, branches: [{ fate_label: "x", weight: 0.5 }, { fate_label: "y", weight: 0.5 }] },
    { decision_id: "b", axis_label: "high", status: "open", consequence: 5, branches: [{ fate_label: "p", weight: 0.6 }, { fate_label: "q", weight: 0.4 }] },
    { decision_id: "c", axis_label: "resolved", status: "resolved", consequence: 9, branches: [{ fate_label: "r", weight: 1 }, { fate_label: "s", weight: 0 }] },
    { decision_id: "d", axis_label: "single", status: "open", consequence: 9, branches: [{ fate_label: "only", weight: 1 }] },
  ]);
  const open = di.openDecisionsFor(slot);
  assert.deepStrictEqual(open.map((d) => d.axis_label), ["high", "low"], "open, >1 branch, by consequence");
}

// No decision_set -> nothing.
assert.deepStrictEqual(di.openDecisionsFor({}), []);
assert.deepStrictEqual(di.openDecisionsFor(undefined), []);

// Chip + percentage.
assert.strictEqual(di.fateChip({ fate_label: "skip", weight: 0.6 }), "skip 60%");
assert.strictEqual(di.pct(0.404), 40);

// shortIO: string example_out is used as-is; long values truncate with an ellipsis.
assert.strictEqual(
  di.shortIO({ example_in: { name: "Ada B" }, example_out: "{'first': 'Ada'}" }),
  '{"name":"Ada B"} -> {\'first\': \'Ada\'}',
);
assert.ok(di.shortIO({ example_in: "x".repeat(200), example_out: "y" }).includes("…"));
assert.strictEqual(di.shortIO({}), "");

console.log("decisionInsight.test.js: all assertions passed");
