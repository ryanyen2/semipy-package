/* Manual verification: feed a realistic synthetic slot (ADAPT with a regression,
 * a multi-case contract, and an applied multi-target effect) through the real
 * derivation + hover-card code and print what a user would actually see. */
const { computeSlotInsight, insightChips } = require("../.test-emit/features/intelligence/slotInsight.js");
const { buildHoverMarkdown } = require("../.test-emit/features/intelligence/explanationCard.js");

const now = Math.floor(Date.now() / 1000);

const slot = {
  slot_id: "64becf049a1b2c3d",
  function_name_base: "clean_record",
  default_branch: "main",
  function_name_base: "clean_record",
  slot_spec: {
    spec_text: "parse the messy customer record into {name, email, tier}",
    enclosing_function_qualname: "clean_record",
    source_span: ["/x/crm_etl.py", 12, 14],
  },
  commits: {
    c1aaaaaaaaaaaaaa: {
      commit_id: "c1aaaaaaaaaaaaaa",
      parent_ids: [],
      decision: "GENERATE",
      timestamp: now - 600,
      message: "initial implementation",
      generated_source: "def clean_record(raw): ...",
    },
    e5f6g7h8aaaaaaaa: {
      commit_id: "e5f6g7h8aaaaaaaa",
      parent_ids: ["c1aaaaaaaaaaaaaa"],
      decision: "ADAPT",
      timestamp: now - 30,
      message: "adapt",
      generated_source: "def clean_record(raw): ...",
      change_record: {
        reason: "previous impl returned an empty dict on emoji-only input",
        decision: "ADAPT",
        parent_commit_id: "c1aaaaaaaaaaaaaa",
        unintended_count: 1,
        n_compared: 3,
        effect_diff: [
          { input_fingerprint: "p1", input_repr: "'  Bob <b@x.io> gold '", old_repr: "{}", new_repr: "{'name':'Bob',...}", intended: true },
          { input_fingerprint: "p2", input_repr: "'Ann <a@x.io> silver'", old_repr: "{'tier':'silver'}", new_repr: "{'tier':'SILVER'}", intended: false },
        ],
      },
    },
  },
  branches: {
    main: { name: "main", head: "c1aaaaaaaaaaaaaa" },
    "adapt-emoji": { name: "adapt-emoji", head: "e5f6g7h8aaaaaaaa" },
  },
  refs: {},
  contract: {
    version: 5,
    cases: {
      i1: { case_id: "i1", kind: "invariant", invariant: "non_empty", status: "active", reason: "must never return empty for a non-empty record" },
      i2: { case_id: "i2", kind: "invariant", invariant: "type_match", expected_type: "dict", status: "active", reason: "callers index name/email/tier" },
      m1: { case_id: "m1", kind: "metamorphic", relation: "whitespace_invariance", status: "active", reason: "leading/trailing spaces must not change the parse" },
      e1: { case_id: "e1", kind: "example", expected_repr: "{'tier':'silver'}", status: "quarantined", reason: "tier casing changed deliberately" },
    },
  },
  ledger: {
    version: 2,
    events: [
      {
        event_id: "ev1aaaaaaaaaaaaa",
        status: "applied",
        timestamp: now - 25,
        applied_effects: [
          { op: "update", target: "db://customers", payload: {}, selector: { id: 42 }, compensation: { op: "update", target: "db://customers" } },
          { op: "append", target: "db://audit_log", payload: {}, compensation: { op: "delete", target: "db://audit_log" } },
        ],
      },
    ],
  },
};

const insight = computeSlotInsight(slot);
console.log("=== computeSlotInsight ===");
console.log(JSON.stringify(insight, null, 2));
console.log("\n=== CodeLens health sentence ===");
console.log(insightChips(insight).join(" · "));
console.log("\n=== Hover Explanation Card (markdown) ===\n");
console.log(buildHoverMarkdown(slot, insight));

// Assertions
const a = [];
function check(name, cond) { a.push((cond ? "PASS" : "FAIL") + " " + name); }
check("decision = ADAPT (newest head)", insight.decision === "ADAPT");
check("commit = e5f6g7h8", insight.commitShort === "e5f6g7h8");
check("contract active = 3", insight.contract.active === 3);
check("contract quarantined = 1", insight.contract.quarantined === 1);
check("change unintended = 1", insight.change.unintended === 1);
check("change hasRegression", insight.change.hasRegression === true);
check("health = danger (regression wins)", insight.health === "danger");
check("effect isEffectful", insight.effect.isEffectful === true);
check("effect 2 targets", insight.effect.targets.length === 2);
check("effect reversible", insight.effect.reversible === true);
check("effect latestEventId set", insight.effect.latestEventId === "ev1aaaaaaaaaaaaa");
console.log("\n=== Assertions ===");
console.log(a.join("\n"));
process.exit(a.some((x) => x.startsWith("FAIL")) ? 1 : 0);
