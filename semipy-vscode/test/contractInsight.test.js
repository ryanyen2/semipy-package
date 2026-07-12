// Node harness for contractInsight: the U5 contract card's pure logic
// (hardness/case/scope/regime facts, the certified/uncertified boundary, and
// the CodeLens/hover surfacing gate). contractInsight.ts has no `vscode`
// import, so it compiles and runs standalone (mirrors decisionInsight.test.js).
"use strict";

const assert = require("assert");
const path = require("path");

const emitRoot = path.resolve(__dirname, "..", ".test-emit-contract-card");
const ci = require(path.join(emitRoot, "features", "contractCard", "contractInsight.js"));

const STEERING_KEYWORDS = ["intent", "given", "by", "unless", "yields", "alt", "verified", "goal", "commits", "because"];

function commit(id, ts) {
  return { commit_id: id, parent_ids: [], generated_source: "x", timestamp: ts, message: "", decision: "GENERATE" };
}

function plainFunctionSlot() {
  // No commits at all -- never went through @semiformal/GENERATE.
  return { slot_id: "s0", function_name_base: "f", commits: {}, branches: {}, refs: {}, default_branch: "main" };
}

function committedNoSurfaceSlot() {
  // Generated once, but the maintainer never recorded any contract state yet.
  return {
    slot_id: "s1", function_name_base: "f", commits: { c1: commit("c1", 1) },
    branches: { main: { name: "main", head: "c1" } }, refs: {}, default_branch: "main",
  };
}

function certifiedSlot() {
  return {
    slot_id: "s2", function_name_base: "f", commits: { c1: commit("c1", 1) },
    branches: { main: { name: "main", head: "c1" } }, refs: {}, default_branch: "main",
    contract: { cases: { case1: { case_id: "case1", kind: "invariant", invariant: "non_empty", status: "active" } } },
    freeze_events: [{ certificate: { epsilon: 0.05, delta: 0.1, gamma: 1, budget_total: 10, budget_spent: 3, held_out_pass_fraction: 1, mdl_gain: 5, licensed: true, refusal_reasons: [] }, node_id: "root", source_len: 10, timestamp: 1 }],
    advisor_state: { scope_predicates: { c1: { conjuncts: [] } } },
  };
}

function d4PartialContractSlot() {
  // Uncertified: active cases exist, but no licensed freeze -- the D4 boundary.
  return {
    slot_id: "s3", function_name_base: "diagram", commits: { c1: commit("c1", 1) },
    branches: { main: { name: "main", head: "c1" } }, refs: {}, default_branch: "main",
    contract: { cases: { inv1: { case_id: "inv1", kind: "invariant", invariant: "non_empty", status: "active" } } },
  };
}

// --- hasContractSurface: gates plain functions and never-contracted slots out ---
assert.strictEqual(ci.hasContractSurface(plainFunctionSlot()), false, "plain function (no commits) has no surface");
assert.strictEqual(ci.hasContractSurface(committedNoSurfaceSlot()), false, "committed but no contract state yet");
assert.strictEqual(ci.hasContractSurface(certifiedSlot()), true, "committed + cases + certificate");
assert.strictEqual(ci.hasContractSurface(d4PartialContractSlot()), true, "committed + cases, no certificate (D4 partial)");

// --- certified/uncertified boundary (D4) ---
assert.strictEqual(ci.isCertified(certifiedSlot()), true);
assert.strictEqual(ci.isCertified(d4PartialContractSlot()), false);
{
  const md = ci.contractCardMarkdown(d4PartialContractSlot());
  assert.ok(md.includes("UNCERTIFIED"), "uncertified slot states the boundary");
  assert.ok(md.includes("partial contract"), "uncertified slot names it a partial contract");
}
{
  const md = ci.contractCardMarkdown(certifiedSlot());
  assert.ok(md.includes("CERTIFIED"), "certified slot states CERTIFIED");
  assert.ok(md.includes("0.05") && md.includes("0.1"), "certified slot names epsilon/delta");
}

// --- case counts ---
assert.deepStrictEqual(ci.caseCounts(certifiedSlot()), { active: 1, superseded: 0, quarantined: 0 });
assert.deepStrictEqual(ci.caseCounts(plainFunctionSlot()), { active: 0, superseded: 0, quarantined: 0 });

// --- scope status: only true when the *active* commit has a minted predicate ---
assert.strictEqual(ci.hasScopePredicate(certifiedSlot()), true);
assert.strictEqual(ci.hasScopePredicate(d4PartialContractSlot()), false);

// --- hardness chip: "plastic" default when no kernel_tree was computed ---
assert.strictEqual(ci.hardnessChip(committedNoSurfaceSlot()), "plastic");
{
  const slot = { ...committedNoSurfaceSlot(), kernel_tree: { node_id: "root", kind: "opaque", hardness: "frozen" } };
  assert.strictEqual(ci.hardnessChip(slot), "frozen");
}

// --- regime count: walks the whole tree, summing guards on every node ---
assert.strictEqual(ci.regimeCount(committedNoSurfaceSlot()), 0, "no kernel_tree -> 0 regimes");
{
  const slot = {
    ...committedNoSurfaceSlot(),
    kernel_tree: {
      node_id: "root", kind: "branch", hardness: "plastic",
      guards: [{ predicate_source: "isinstance(x, int)" }],
      children: [
        { node_id: "c0", kind: "opaque", hardness: "plastic", guards: [] },
        { node_id: "c1", kind: "branch", hardness: "plastic", guards: [{ predicate_source: "x > 0" }] },
      ],
    },
  };
  assert.strictEqual(ci.regimeCount(slot), 2, "sums guards across root + descendants");
}

// --- relations: distinct, active-only, metamorphic cases ---
{
  const slot = {
    ...committedNoSurfaceSlot(),
    contract: {
      cases: {
        m1: { case_id: "m1", kind: "metamorphic", relation: "commutative", status: "active" },
        m2: { case_id: "m2", kind: "metamorphic", relation: "commutative", status: "active" },
        m3: { case_id: "m3", kind: "metamorphic", relation: "idempotent", status: "quarantined" },
      },
    },
  };
  assert.deepStrictEqual(ci.relationsFor(slot), ["commutative"]);
}

// --- codeLensTitle: hardness, case count, scope status, regime count -- and
// nothing that duplicates the `#<` steering vocabulary (KTD-3). ---
{
  const title = ci.codeLensTitle(certifiedSlot());
  assert.ok(title.includes("plastic"));
  assert.ok(title.includes("1 active case"));
  assert.ok(title.includes("scope minted"));
  for (const kw of STEERING_KEYWORDS) {
    assert.ok(!new RegExp(`\\b${kw}:`).test(title), `codeLensTitle must not duplicate #< field "${kw}:"`);
  }
}
{
  const md = ci.contractCardMarkdown(certifiedSlot());
  for (const kw of STEERING_KEYWORDS) {
    assert.ok(!new RegExp(`\\b${kw}:`).test(md), `contractCardMarkdown must not duplicate #< field "${kw}:"`);
  }
  assert.ok(md.includes("semipy.disputeContract"), "hover offers the dispute action");
}

console.log("contractInsight.test.js: all assertions passed");
