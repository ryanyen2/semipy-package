// Node harness for slotInsight: the freeze-certificate summary (Phase 7)
// computeSlotInsight surfaces from Slot.freeze_events, plus the PROMOTE glyph.
// slotInsight.ts has no `vscode` import, so it compiles and runs standalone.
"use strict";

const assert = require("assert");
const path = require("path");

const emitRoot = path.resolve(__dirname, "..", ".test-emit-slot-insight");
const si = require(path.join(emitRoot, "features", "intelligence", "slotInsight.js"));

function cert(overrides) {
  return Object.assign(
    {
      epsilon: 0.1,
      delta: 0.05,
      gamma: 1.0,
      budget_total: 30,
      budget_spent: 12,
      held_out_pass_fraction: 1.0,
      mdl_gain: 42,
      licensed: true,
      refusal_reasons: [],
    },
    overrides,
  );
}

function baseSlot(decision) {
  return {
    commits: { c1: { commit_id: "c1", decision, timestamp: 100 } },
    branches: { main: { name: "main", head: "c1" } },
    refs: {},
  };
}

function slotWithFreezeEvents(events) {
  return Object.assign(baseSlot("PROMOTE"), { freeze_events: events });
}

// No freeze_events -> no freeze insight, but the slot still resolves.
{
  const insight = si.computeSlotInsight(baseSlot("REUSE"));
  assert.strictEqual(insight.freeze, undefined);
}

// A single refused attempt: latestLicensed false, reasons carried through.
{
  const insight = si.computeSlotInsight(
    slotWithFreezeEvents([
      { certificate: cert({ licensed: false, refusal_reasons: ["held-out match 0.40 < required"] }), node_id: "", source_len: 0, timestamp: 1 },
    ]),
  );
  assert.ok(insight.freeze);
  assert.strictEqual(insight.freeze.attempts, 1);
  assert.strictEqual(insight.freeze.licensedCount, 0);
  assert.strictEqual(insight.freeze.latestLicensed, false);
  assert.deepStrictEqual(insight.freeze.refusalReasons, ["held-out match 0.40 < required"]);
}

// Two attempts, the latest licensed: attempts/licensedCount aggregate, latest wins for the rest.
{
  const insight = si.computeSlotInsight(
    slotWithFreezeEvents([
      { certificate: cert({ licensed: false, refusal_reasons: ["MDL gate"] }), node_id: "", source_len: 0, timestamp: 1 },
      { certificate: cert({ licensed: true, budget_spent: 20 }), node_id: "", source_len: 0, timestamp: 2 },
    ]),
  );
  assert.strictEqual(insight.freeze.attempts, 2);
  assert.strictEqual(insight.freeze.licensedCount, 1);
  assert.strictEqual(insight.freeze.latestLicensed, true);
  assert.strictEqual(insight.freeze.budgetSpent, 20);
  assert.strictEqual(insight.freeze.timestamp, 2);
}

// PROMOTE gets its own glyph, distinct from the default.
assert.strictEqual(si.decisionGlyph("PROMOTE"), "●");
assert.notStrictEqual(si.decisionGlyph("PROMOTE"), si.decisionGlyph("unknown_decision"));

console.log("slotInsight.test.js: all assertions passed");
