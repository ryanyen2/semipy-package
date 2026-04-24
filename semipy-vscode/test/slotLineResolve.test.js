// Lightweight node harness for slotLineResolve — verifies US-001:
// two @semiformal methods in the same file -> each slot's codeLensLine0 is
// inside its own method, not the topmost @semiformal.
"use strict";

const path = require("path");
const Module = require("module");

// --- stub the vscode module before requiring compiled emit ---
const vscodeStub = {};
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, isMain, options) {
  if (request === "vscode") {
    return "vscode-stub";
  }
  return originalResolve.call(this, request, parent, isMain, options);
};
require.cache["vscode-stub"] = {
  id: "vscode-stub",
  filename: "vscode-stub",
  loaded: true,
  exports: vscodeStub,
};

const emitRoot = path.resolve(__dirname, "..", ".test-emit");
const slotLineResolve = require(
  path.join(emitRoot, "features", "slotAnnotations", "slotLineResolve.js"),
);

function makeDoc(filePath, text) {
  const lines = text.split(/\r?\n/);
  return {
    uri: { fsPath: filePath },
    getText: () => text,
    lineAt: (i) => ({ text: lines[i] ?? "" }),
    get lineCount() {
      return lines.length;
    },
  };
}

function makeSlot(filePath, sourceSpan, enclosingSpan, specText) {
  return {
    slot_id: "x",
    slot_spec: {
      slot_id: "x",
      source_span: sourceSpan,
      enclosing_function_span: enclosingSpan,
      spec_text: specText,
    },
  };
}

const text = [
  "from semipy import semiformal",
  "",
  "class Pipeline:",
  "    @semiformal",                                 // 4
  "    def classify_body(self, body: str) -> str:",  // 5
  "        #> classify body into family",            // 6
  "        family = ...",                            // 7
  "        return family",                           // 8
  "",                                                // 9
  "    @semiformal",                                 // 10
  "    def infer_templates(self, bodies) -> list:",  // 11
  "        #> infer anchored regex templates",       // 12
  "        templates = ...",                         // 13
  "        return templates",                        // 14
].join("\n");

const filePath = "/tmp/fake_file.py";
const doc = makeDoc(filePath, text);

// Slot 1: inside classify_body. source_span line 6.
const s1 = makeSlot(
  filePath,
  [filePath, 6, 6],
  [filePath, 4, 8],
  "classify body into family",
);
// Slot 2: inside infer_templates. source_span line 12.
const s2 = makeSlot(
  filePath,
  [filePath, 12, 12],
  [filePath, 10, 14],
  "infer anchored regex templates",
);

const r1 = slotLineResolve.resolveSlotUiLines(doc, s1);
const r2 = slotLineResolve.resolveSlotUiLines(doc, s2);

const errs = [];
if (!r1) errs.push("slot1 returned undefined");
if (!r2) errs.push("slot2 returned undefined");
if (r1 && r2) {
  // Each slot should anchor inside its own enclosing function.
  if (r1.codeLensLine0 !== 3) {
    errs.push(`slot1 codeLensLine0 ${r1.codeLensLine0} !== 3 (@semiformal classify_body line index)`);
  }
  if (r2.codeLensLine0 !== 9) {
    errs.push(`slot2 codeLensLine0 ${r2.codeLensLine0} !== 9 (@semiformal infer_templates line index)`);
  }
  if (r1.codeLensLine0 === r2.codeLensLine0) {
    errs.push("both slots collapsed onto the same anchor (topmost @semiformal bug)");
  }
}

if (errs.length) {
  console.error("FAIL:\n  " + errs.join("\n  "));
  process.exit(1);
} else {
  console.log(
    `ok slot1 codeLensLine0=${r1.codeLensLine0} slot2 codeLensLine0=${r2.codeLensLine0}`,
  );
}
