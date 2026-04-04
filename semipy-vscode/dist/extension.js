"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/extension.ts
var extension_exports = {};
__export(extension_exports, {
  activate: () => activate,
  deactivate: () => deactivate
});
module.exports = __toCommonJS(extension_exports);
var path7 = __toESM(require("path"));
var import_vscode13 = require("vscode");

// src/data/portalLoader.ts
var fs = __toESM(require("fs"));
var path = __toESM(require("path"));

// src/data/sessionId.ts
var crypto = __toESM(require("crypto"));
function sessionIdFromFilename(filename) {
  if (!filename || filename === "<unknown>") {
    return crypto.createHash("sha256").update("<unknown>").digest("hex").slice(0, 16);
  }
  let normalized = filename.replace(/\\/g, "/").trim().toLowerCase();
  let base = normalized.includes("/") ? normalized.split("/").pop() ?? normalized : normalized;
  if (base.endsWith(".py")) {
    base = base.slice(0, -3);
  }
  if (!base) {
    base = normalized;
  }
  return crypto.createHash("sha256").update(base).digest("hex").slice(0, 16);
}

// src/data/portalLoader.ts
function findWorkspaceRootContainingSemiformal(filePath) {
  let dir = path.dirname(filePath);
  const { root } = path.parse(filePath);
  while (true) {
    const candidate = path.join(dir, ".semiformal");
    try {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
        return dir;
      }
    } catch {
    }
    if (dir === root) {
      break;
    }
    dir = path.dirname(dir);
  }
  return void 0;
}
function resolvePortalAnchorSourcePath(actualFilename) {
  const env = (process.env.SEMIPY_SESSION_SOURCE || "").trim();
  if (env) {
    return path.resolve(env);
  }
  const norm = actualFilename.replace(/\\/g, "/").toLowerCase();
  if (norm.includes("ipykernel")) {
    try {
      return process.cwd();
    } catch {
      return actualFilename;
    }
  }
  try {
    return path.resolve(actualFilename);
  } catch {
    return actualFilename;
  }
}
function expectedPortalJsonPath(sourceFilePath) {
  const root = findWorkspaceRootContainingSemiformal(sourceFilePath);
  if (!root) {
    return void 0;
  }
  const anchor = resolvePortalAnchorSourcePath(sourceFilePath);
  const sid = sessionIdFromFilename(anchor);
  return path.join(root, ".semiformal", `${sid}.portal.json`);
}
function loadPortalJson(portalPath) {
  try {
    const raw = fs.readFileSync(portalPath, "utf8");
    return JSON.parse(raw);
  } catch {
    return void 0;
  }
}
function portalMatchesEditorFile(portal, editorFsPath) {
  const anchor = resolvePortalAnchorSourcePath(editorFsPath);
  const sid = sessionIdFromFilename(anchor);
  if (portal.session_id && portal.session_id === sid) {
    return true;
  }
  const sf = portal.source_file || "";
  if (!sf) {
    return false;
  }
  try {
    if (path.normalize(sf) === path.normalize(editorFsPath)) {
      return true;
    }
    if (path.basename(sf) === path.basename(editorFsPath)) {
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

// src/features/commentOpacity/opacityDecorations.ts
var import_vscode = require("vscode");

// src/util/hashArrowDetect.ts
function isReasoningLine(line) {
  const stripped = line.replace(/^\s+/, "");
  return stripped.startsWith("#<") || stripped.startsWith("# <");
}
function hashArrowPrefixRange(line) {
  const m = line.match(/^(\s*)((?:#\s*>))/);
  if (!m || m.index === void 0) {
    return null;
  }
  const lead = m[1].length;
  const pref = m[2].length;
  return { start: lead, end: lead + pref };
}

// src/features/commentOpacity/opacityDecorations.ts
function createOpacityDecorationTypes() {
  const reasoningDim = import_vscode.window.createTextEditorDecorationType({
    isWholeLine: true,
    opacity: "0.65",
    overviewRulerColor: "rgba(120,120,120,0.35)",
    overviewRulerLane: import_vscode.OverviewRulerLane.Left
  });
  return { reasoningDim };
}
function refreshOpacityDecorations(editor, reasoningDim) {
  if (editor.document.languageId !== "python") {
    editor.setDecorations(reasoningDim, []);
    return;
  }
  const text = editor.document.getText();
  const lines = text.split(/\r?\n/);
  const reasoning = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (isReasoningLine(line)) {
      reasoning.push(editor.document.lineAt(i).range);
    }
  }
  editor.setDecorations(reasoningDim, reasoning);
}

// node_modules/.pnpm/diff@5.2.2/node_modules/diff/lib/index.mjs
function Diff() {
}
Diff.prototype = {
  diff: function diff(oldString, newString) {
    var _options$timeout;
    var options = arguments.length > 2 && arguments[2] !== void 0 ? arguments[2] : {};
    var callback = options.callback;
    if (typeof options === "function") {
      callback = options;
      options = {};
    }
    this.options = options;
    var self = this;
    function done(value) {
      if (callback) {
        setTimeout(function() {
          callback(void 0, value);
        }, 0);
        return true;
      } else {
        return value;
      }
    }
    oldString = this.castInput(oldString);
    newString = this.castInput(newString);
    oldString = this.removeEmpty(this.tokenize(oldString));
    newString = this.removeEmpty(this.tokenize(newString));
    var newLen = newString.length, oldLen = oldString.length;
    var editLength = 1;
    var maxEditLength = newLen + oldLen;
    if (options.maxEditLength) {
      maxEditLength = Math.min(maxEditLength, options.maxEditLength);
    }
    var maxExecutionTime = (_options$timeout = options.timeout) !== null && _options$timeout !== void 0 ? _options$timeout : Infinity;
    var abortAfterTimestamp = Date.now() + maxExecutionTime;
    var bestPath = [{
      oldPos: -1,
      lastComponent: void 0
    }];
    var newPos = this.extractCommon(bestPath[0], newString, oldString, 0);
    if (bestPath[0].oldPos + 1 >= oldLen && newPos + 1 >= newLen) {
      return done([{
        value: this.join(newString),
        count: newString.length
      }]);
    }
    var minDiagonalToConsider = -Infinity, maxDiagonalToConsider = Infinity;
    function execEditLength() {
      for (var diagonalPath = Math.max(minDiagonalToConsider, -editLength); diagonalPath <= Math.min(maxDiagonalToConsider, editLength); diagonalPath += 2) {
        var basePath = void 0;
        var removePath = bestPath[diagonalPath - 1], addPath = bestPath[diagonalPath + 1];
        if (removePath) {
          bestPath[diagonalPath - 1] = void 0;
        }
        var canAdd = false;
        if (addPath) {
          var addPathNewPos = addPath.oldPos - diagonalPath;
          canAdd = addPath && 0 <= addPathNewPos && addPathNewPos < newLen;
        }
        var canRemove = removePath && removePath.oldPos + 1 < oldLen;
        if (!canAdd && !canRemove) {
          bestPath[diagonalPath] = void 0;
          continue;
        }
        if (!canRemove || canAdd && removePath.oldPos + 1 < addPath.oldPos) {
          basePath = self.addToPath(addPath, true, void 0, 0);
        } else {
          basePath = self.addToPath(removePath, void 0, true, 1);
        }
        newPos = self.extractCommon(basePath, newString, oldString, diagonalPath);
        if (basePath.oldPos + 1 >= oldLen && newPos + 1 >= newLen) {
          return done(buildValues(self, basePath.lastComponent, newString, oldString, self.useLongestToken));
        } else {
          bestPath[diagonalPath] = basePath;
          if (basePath.oldPos + 1 >= oldLen) {
            maxDiagonalToConsider = Math.min(maxDiagonalToConsider, diagonalPath - 1);
          }
          if (newPos + 1 >= newLen) {
            minDiagonalToConsider = Math.max(minDiagonalToConsider, diagonalPath + 1);
          }
        }
      }
      editLength++;
    }
    if (callback) {
      (function exec() {
        setTimeout(function() {
          if (editLength > maxEditLength || Date.now() > abortAfterTimestamp) {
            return callback();
          }
          if (!execEditLength()) {
            exec();
          }
        }, 0);
      })();
    } else {
      while (editLength <= maxEditLength && Date.now() <= abortAfterTimestamp) {
        var ret = execEditLength();
        if (ret) {
          return ret;
        }
      }
    }
  },
  addToPath: function addToPath(path8, added, removed, oldPosInc) {
    var last = path8.lastComponent;
    if (last && last.added === added && last.removed === removed) {
      return {
        oldPos: path8.oldPos + oldPosInc,
        lastComponent: {
          count: last.count + 1,
          added,
          removed,
          previousComponent: last.previousComponent
        }
      };
    } else {
      return {
        oldPos: path8.oldPos + oldPosInc,
        lastComponent: {
          count: 1,
          added,
          removed,
          previousComponent: last
        }
      };
    }
  },
  extractCommon: function extractCommon(basePath, newString, oldString, diagonalPath) {
    var newLen = newString.length, oldLen = oldString.length, oldPos = basePath.oldPos, newPos = oldPos - diagonalPath, commonCount = 0;
    while (newPos + 1 < newLen && oldPos + 1 < oldLen && this.equals(newString[newPos + 1], oldString[oldPos + 1])) {
      newPos++;
      oldPos++;
      commonCount++;
    }
    if (commonCount) {
      basePath.lastComponent = {
        count: commonCount,
        previousComponent: basePath.lastComponent
      };
    }
    basePath.oldPos = oldPos;
    return newPos;
  },
  equals: function equals(left, right) {
    if (this.options.comparator) {
      return this.options.comparator(left, right);
    } else {
      return left === right || this.options.ignoreCase && left.toLowerCase() === right.toLowerCase();
    }
  },
  removeEmpty: function removeEmpty(array) {
    var ret = [];
    for (var i = 0; i < array.length; i++) {
      if (array[i]) {
        ret.push(array[i]);
      }
    }
    return ret;
  },
  castInput: function castInput(value) {
    return value;
  },
  tokenize: function tokenize(value) {
    return value.split("");
  },
  join: function join2(chars) {
    return chars.join("");
  }
};
function buildValues(diff2, lastComponent, newString, oldString, useLongestToken) {
  var components = [];
  var nextComponent;
  while (lastComponent) {
    components.push(lastComponent);
    nextComponent = lastComponent.previousComponent;
    delete lastComponent.previousComponent;
    lastComponent = nextComponent;
  }
  components.reverse();
  var componentPos = 0, componentLen = components.length, newPos = 0, oldPos = 0;
  for (; componentPos < componentLen; componentPos++) {
    var component = components[componentPos];
    if (!component.removed) {
      if (!component.added && useLongestToken) {
        var value = newString.slice(newPos, newPos + component.count);
        value = value.map(function(value2, i) {
          var oldValue = oldString[oldPos + i];
          return oldValue.length > value2.length ? oldValue : value2;
        });
        component.value = diff2.join(value);
      } else {
        component.value = diff2.join(newString.slice(newPos, newPos + component.count));
      }
      newPos += component.count;
      if (!component.added) {
        oldPos += component.count;
      }
    } else {
      component.value = diff2.join(oldString.slice(oldPos, oldPos + component.count));
      oldPos += component.count;
      if (componentPos && components[componentPos - 1].added) {
        var tmp = components[componentPos - 1];
        components[componentPos - 1] = components[componentPos];
        components[componentPos] = tmp;
      }
    }
  }
  var finalComponent = components[componentLen - 1];
  if (componentLen > 1 && typeof finalComponent.value === "string" && (finalComponent.added || finalComponent.removed) && diff2.equals("", finalComponent.value)) {
    components[componentLen - 2].value += finalComponent.value;
    components.pop();
  }
  return components;
}
var characterDiff = new Diff();
var extendedWordChars = /^[A-Za-z\xC0-\u02C6\u02C8-\u02D7\u02DE-\u02FF\u1E00-\u1EFF]+$/;
var reWhitespace = /\S/;
var wordDiff = new Diff();
wordDiff.equals = function(left, right) {
  if (this.options.ignoreCase) {
    left = left.toLowerCase();
    right = right.toLowerCase();
  }
  return left === right || this.options.ignoreWhitespace && !reWhitespace.test(left) && !reWhitespace.test(right);
};
wordDiff.tokenize = function(value) {
  var tokens = value.split(/([^\S\r\n]+|[()[\]{}'"\r\n]|\b)/);
  for (var i = 0; i < tokens.length - 1; i++) {
    if (!tokens[i + 1] && tokens[i + 2] && extendedWordChars.test(tokens[i]) && extendedWordChars.test(tokens[i + 2])) {
      tokens[i] += tokens[i + 2];
      tokens.splice(i + 1, 2);
      i--;
    }
  }
  return tokens;
};
var lineDiff = new Diff();
lineDiff.tokenize = function(value) {
  if (this.options.stripTrailingCr) {
    value = value.replace(/\r\n/g, "\n");
  }
  var retLines = [], linesAndNewlines = value.split(/(\n|\r\n)/);
  if (!linesAndNewlines[linesAndNewlines.length - 1]) {
    linesAndNewlines.pop();
  }
  for (var i = 0; i < linesAndNewlines.length; i++) {
    var line = linesAndNewlines[i];
    if (i % 2 && !this.options.newlineIsToken) {
      retLines[retLines.length - 1] += line;
    } else {
      if (this.options.ignoreWhitespace) {
        line = line.trim();
      }
      retLines.push(line);
    }
  }
  return retLines;
};
function diffLines(oldStr, newStr, callback) {
  return lineDiff.diff(oldStr, newStr, callback);
}
var sentenceDiff = new Diff();
sentenceDiff.tokenize = function(value) {
  return value.split(/(\S.+?[.!?])(?=\s+|$)/);
};
var cssDiff = new Diff();
cssDiff.tokenize = function(value) {
  return value.split(/([{}:;,]|\s+)/);
};
function _typeof(obj) {
  "@babel/helpers - typeof";
  if (typeof Symbol === "function" && typeof Symbol.iterator === "symbol") {
    _typeof = function(obj2) {
      return typeof obj2;
    };
  } else {
    _typeof = function(obj2) {
      return obj2 && typeof Symbol === "function" && obj2.constructor === Symbol && obj2 !== Symbol.prototype ? "symbol" : typeof obj2;
    };
  }
  return _typeof(obj);
}
var objectPrototypeToString = Object.prototype.toString;
var jsonDiff = new Diff();
jsonDiff.useLongestToken = true;
jsonDiff.tokenize = lineDiff.tokenize;
jsonDiff.castInput = function(value) {
  var _this$options = this.options, undefinedReplacement = _this$options.undefinedReplacement, _this$options$stringi = _this$options.stringifyReplacer, stringifyReplacer = _this$options$stringi === void 0 ? function(k, v) {
    return typeof v === "undefined" ? undefinedReplacement : v;
  } : _this$options$stringi;
  return typeof value === "string" ? value : JSON.stringify(canonicalize(value, null, null, stringifyReplacer), stringifyReplacer, "  ");
};
jsonDiff.equals = function(left, right) {
  return Diff.prototype.equals.call(jsonDiff, left.replace(/,([\r\n])/g, "$1"), right.replace(/,([\r\n])/g, "$1"));
};
function canonicalize(obj, stack, replacementStack, replacer, key) {
  stack = stack || [];
  replacementStack = replacementStack || [];
  if (replacer) {
    obj = replacer(key, obj);
  }
  var i;
  for (i = 0; i < stack.length; i += 1) {
    if (stack[i] === obj) {
      return replacementStack[i];
    }
  }
  var canonicalizedObj;
  if ("[object Array]" === objectPrototypeToString.call(obj)) {
    stack.push(obj);
    canonicalizedObj = new Array(obj.length);
    replacementStack.push(canonicalizedObj);
    for (i = 0; i < obj.length; i += 1) {
      canonicalizedObj[i] = canonicalize(obj[i], stack, replacementStack, replacer, key);
    }
    stack.pop();
    replacementStack.pop();
    return canonicalizedObj;
  }
  if (obj && obj.toJSON) {
    obj = obj.toJSON();
  }
  if (_typeof(obj) === "object" && obj !== null) {
    stack.push(obj);
    canonicalizedObj = {};
    replacementStack.push(canonicalizedObj);
    var sortedKeys = [], _key;
    for (_key in obj) {
      if (obj.hasOwnProperty(_key)) {
        sortedKeys.push(_key);
      }
    }
    sortedKeys.sort();
    for (i = 0; i < sortedKeys.length; i += 1) {
      _key = sortedKeys[i];
      canonicalizedObj[_key] = canonicalize(obj[_key], stack, replacementStack, replacer, _key);
    }
    stack.pop();
    replacementStack.pop();
  } else {
    canonicalizedObj = obj;
  }
  return canonicalizedObj;
}
var arrayDiff = new Diff();
arrayDiff.tokenize = function(value) {
  return value.slice();
};
arrayDiff.join = arrayDiff.removeEmpty = function(value) {
  return value;
};

// src/features/commentOpacity/signFlipListener.ts
var import_vscode2 = require("vscode");
function splitLines(text) {
  const t = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (t === "") {
    return [];
  }
  return t.split("\n");
}
function rewriteReasoningPrefixToSpec(line) {
  const m = line.match(/^(\s*)(#\s*<)/);
  if (!m) {
    return null;
  }
  const leadLen = m[1].length;
  const prefixLen = m[2].length;
  return line.slice(0, leadLen) + "#>" + line.slice(leadLen + prefixLen);
}
var SignFlipCoordinator = class {
  constructor(enabled) {
    this.enabled = enabled;
  }
  previousText = /* @__PURE__ */ new Map();
  applying = /* @__PURE__ */ new Set();
  attach() {
    const sub = import_vscode2.workspace.onDidChangeTextDocument((e) => this.onChange(e));
    return {
      dispose: () => sub.dispose()
    };
  }
  onChange(e) {
    if (e.document.languageId !== "python") {
      return;
    }
    const uriKey = e.document.uri.toString();
    if (e.reason === import_vscode2.TextDocumentChangeReason.Undo || e.reason === import_vscode2.TextDocumentChangeReason.Redo) {
      this.previousText.set(uriKey, e.document.getText());
      return;
    }
    if (this.applying.has(uriKey)) {
      this.previousText.set(uriKey, e.document.getText());
      return;
    }
    if (!this.enabled()) {
      this.previousText.set(uriKey, e.document.getText());
      return;
    }
    const before = this.previousText.get(uriKey);
    const after = e.document.getText();
    this.previousText.set(uriKey, after);
    if (before === void 0 || before === after) {
      return;
    }
    const flips = collectFlipLineNumbers1Based(before, after);
    if (flips.length === 0) {
      return;
    }
    const edit = new import_vscode2.WorkspaceEdit();
    for (const line1 of flips) {
      if (line1 < 1 || line1 > e.document.lineCount) {
        continue;
      }
      const line = e.document.lineAt(line1 - 1);
      const fixed = rewriteReasoningPrefixToSpec(line.text);
      if (fixed === null || fixed === line.text) {
        continue;
      }
      edit.replace(e.document.uri, line.range, fixed);
    }
    if (edit.size === 0) {
      return;
    }
    this.applying.add(uriKey);
    void import_vscode2.workspace.applyEdit(edit).then(
      (ok) => {
        this.applying.delete(uriKey);
        if (ok) {
          this.previousText.set(uriKey, e.document.getText());
        }
      },
      () => {
        this.applying.delete(uriKey);
      }
    );
  }
  seedDocument(doc) {
    this.previousText.set(doc.uri.toString(), doc.getText());
  }
};
function collectFlipLineNumbers1Based(before, after) {
  const b = splitLines(before);
  const a = splitLines(after);
  if (b.length === a.length) {
    const out2 = [];
    for (let i2 = 0; i2 < a.length; i2++) {
      if (isReasoningLine(b[i2]) && isReasoningLine(a[i2]) && b[i2] !== a[i2]) {
        out2.push(i2 + 1);
      }
    }
    return out2;
  }
  const out = [];
  const parts = diffLines(before, after);
  let newLine = 1;
  let i = 0;
  while (i < parts.length) {
    const p = parts[i];
    if (!p.added && !p.removed) {
      newLine += splitLines(p.value).length;
      i += 1;
      continue;
    }
    if (p.removed && i + 1 < parts.length && parts[i + 1].added) {
      const oldL = splitLines(p.value);
      const newL = splitLines(parts[i + 1].value);
      const n = Math.min(oldL.length, newL.length);
      for (let j = 0; j < n; j++) {
        if (isReasoningLine(oldL[j]) && isReasoningLine(newL[j]) && oldL[j] !== newL[j]) {
          out.push(newLine + j);
        }
      }
      newLine += newL.length;
      i += 2;
      continue;
    }
    if (p.added) {
      newLine += splitLines(p.value).length;
      i += 1;
      continue;
    }
    if (p.removed) {
      i += 1;
      continue;
    }
    i += 1;
  }
  return out;
}

// src/features/phraseHighlight/phraseHoverProvider.ts
var import_vscode3 = require("vscode");
var import_vscode4 = require("vscode");

// src/data/sketchLoader.ts
var fs2 = __toESM(require("fs"));
var path2 = __toESM(require("path"));
function sketchLibraryPath(semiformalRoot) {
  return path2.join(semiformalRoot, ".semiformal", "sketch_library.json");
}
function loadSketchLibrary(semiformalRoot) {
  const p = sketchLibraryPath(semiformalRoot);
  try {
    const raw = fs2.readFileSync(p, "utf8");
    return JSON.parse(raw);
  } catch {
    return void 0;
  }
}
function bindingById(lib, bindingId) {
  if (!lib?.bindings || !bindingId) {
    return void 0;
  }
  return lib.bindings[bindingId];
}

// src/features/splitEditor/portalCommit.ts
function activeCommitFromPortalSlot(slot) {
  let best;
  let bestTs = -1;
  for (const b of Object.values(slot.branches)) {
    const c = slot.commits[b.head];
    if (c !== void 0 && c.timestamp > bestTs) {
      best = c;
      bestTs = c.timestamp;
    }
  }
  if (best !== void 0) {
    return best;
  }
  if (!slot.refs || !slot.commits) {
    return void 0;
  }
  const ids = new Set(Object.values(slot.refs));
  const candidates = [...ids].map((id) => slot.commits[id]).filter(Boolean);
  if (candidates.length === 0) {
    return void 0;
  }
  return candidates.reduce((a, b) => a.timestamp >= b.timestamp ? a : b);
}

// src/features/phraseHighlight/phraseHoverProvider.ts
function createPhraseHoverProvider(getPortal, getSemiformalRoot) {
  return {
    provideHover(document, pos) {
      if (document.languageId !== "python") {
        return void 0;
      }
      const portal = getPortal();
      const root = getSemiformalRoot();
      if (!portal || !root) {
        return void 0;
      }
      const line1 = pos.line + 1;
      const lineText = document.lineAt(pos.line).text;
      const pref = hashArrowPrefixRange(lineText);
      if (!pref || pos.character < pref.end) {
        return void 0;
      }
      const lib = loadSketchLibrary(root);
      const suffix = lineText.slice(pref.end);
      const rel = pos.character - pref.end;
      for (const slot of Object.values(portal.slots)) {
        const head = activeCommitFromPortalSlot(slot);
        const bid = head?.binding_id || "";
        if (!bid) {
          continue;
        }
        const binding = bindingById(lib, bid);
        if (!binding?.phrases?.length) {
          continue;
        }
        const span = slot.slot_spec?.source_span;
        if (!span || span.length < 3) {
          continue;
        }
        const [, a, b] = span;
        if (line1 < a || line1 > b) {
          continue;
        }
        const sorted = [...binding.phrases].sort((x, y) => y.text.length - x.text.length);
        for (const p of sorted) {
          const t = (p.text || "").trim();
          if (!t) {
            continue;
          }
          let idx = 0;
          while (idx < suffix.length) {
            const at = suffix.indexOf(t, idx);
            if (at < 0) {
              break;
            }
            if (rel >= at && rel < at + t.length) {
              const md = new import_vscode4.MarkdownString();
              md.appendMarkdown(`**${p.role}**

`);
              md.appendMarkdown(`code referent: \`${p.code_referent || ""}\`

`);
              if (p.hole_name) {
                md.appendMarkdown(`hole: \`${p.hole_name}\`

`);
              }
              if (p.safe_swap_set?.length) {
                md.appendMarkdown(`safe swaps: ${p.safe_swap_set.map((s) => `\`${s}\``).join(", ")}`);
              }
              return new import_vscode3.Hover(md);
            }
            idx = at + 1;
          }
        }
      }
      return void 0;
    }
  };
}

// src/features/phraseHighlight/phraseDecorations.ts
var import_vscode5 = require("vscode");
var ROLE_ORDER = ["operation", "param", "operator", "connective"];
function createPhraseDecorationTypes() {
  const out = {};
  for (const role of ROLE_ORDER) {
    out[role] = import_vscode5.window.createTextEditorDecorationType({
      rangeBehavior: import_vscode5.DecorationRangeBehavior.ClosedClosed,
      fontWeight: role === "operation" ? "bold" : void 0
    });
  }
  return out;
}
function sortPhrasesLongestFirst(phrases) {
  return [...phrases].sort((a, b) => (b.text || "").length - (a.text || "").length);
}
function phraseSpansInSuffix(suffix, phrases) {
  const sorted = sortPhrasesLongestFirst(phrases);
  const used = [];
  const spans = [];
  const overlaps = (s, e) => used.some((u) => !(e <= u.start || s >= u.end));
  for (const p of sorted) {
    const t = (p.text || "").trim();
    if (!t) {
      continue;
    }
    let search = 0;
    while (search < suffix.length) {
      const pos = suffix.indexOf(t, search);
      if (pos < 0) {
        break;
      }
      const end = pos + t.length;
      if (!overlaps(pos, end)) {
        used.push({ start: pos, end });
        spans.push({ start: pos, end, role: p.role || "param" });
        break;
      }
      search = pos + 1;
    }
  }
  return spans;
}
function refreshPhraseDecorations(editor, portal, semiformalRoot, types) {
  for (const t of Object.values(types)) {
    editor.setDecorations(t, []);
  }
  if (!portal || !semiformalRoot) {
    return;
  }
  const lib = loadSketchLibrary(semiformalRoot);
  const doc = editor.document;
  const full = doc.getText();
  const lines = full.split(/\r?\n/);
  const rangesByRole = {};
  for (const r of ROLE_ORDER) {
    rangesByRole[r] = [];
  }
  for (const slot of Object.values(portal.slots)) {
    const head = activeCommitFromPortalSlot(slot);
    const bid = head?.binding_id || "";
    if (!bid) {
      continue;
    }
    const binding = bindingById(lib, bid);
    if (!binding?.phrases?.length) {
      continue;
    }
    const span = slot.slot_spec?.source_span;
    if (!span || span.length < 3) {
      continue;
    }
    const [, start1, end1] = span;
    for (let lineIdx = start1 - 1; lineIdx <= end1 - 1; lineIdx++) {
      if (lineIdx < 0 || lineIdx >= lines.length) {
        continue;
      }
      const line = lines[lineIdx];
      const pref = hashArrowPrefixRange(line);
      if (!pref) {
        continue;
      }
      const suffix = line.slice(pref.end);
      const spans = phraseSpansInSuffix(suffix, binding.phrases);
      const lineObj = doc.lineAt(lineIdx);
      for (const sp of spans) {
        const role = ROLE_ORDER.includes(sp.role) ? sp.role : "param";
        const startCol = pref.end + sp.start;
        const endCol = pref.end + sp.end;
        const r = new import_vscode5.Range(
          new import_vscode5.Position(lineIdx, startCol),
          new import_vscode5.Position(lineIdx, endCol)
        );
        rangesByRole[role].push(r);
      }
    }
  }
  for (const role of ROLE_ORDER) {
    const t = types[role];
    if (t) {
      editor.setDecorations(t, rangesByRole[role] || []);
    }
  }
}

// src/features/splitEditor/linkedHighlight.ts
var path4 = __toESM(require("path"));
var import_vscode6 = require("vscode");

// src/features/splitEditor/correspondenceMap.ts
var path3 = __toESM(require("path"));

// src/data/dispatchLoader.ts
function parseSpecMapEntry(entry) {
  const idx = entry.indexOf(":");
  if (idx <= 0) {
    return void 0;
  }
  const fn = entry.slice(0, idx);
  const rest = entry.slice(idx + 1);
  const m = rest.match(/^(\d+)-(\d+)$/);
  if (!m) {
    return void 0;
  }
  return {
    fn,
    startLine: parseInt(m[1], 10),
    endLine: parseInt(m[2], 10)
  };
}

// src/features/splitEditor/correspondenceMap.ts
function pathsEqual(a, b) {
  try {
    return path3.normalize(a) === path3.normalize(b);
  } catch {
    return a === b;
  }
}
function findSlotForSourceLine(portal, sourceFsPath, line1) {
  for (const slot of Object.values(portal.slots)) {
    const sp = slot.slot_spec;
    const span = sp?.source_span;
    if (!span || span.length < 3) {
      continue;
    }
    const [fn, start, end] = span;
    if (!pathsEqual(fn, sourceFsPath) && path3.basename(fn) !== path3.basename(sourceFsPath)) {
      continue;
    }
    if (line1 >= start && line1 <= end) {
      return slot;
    }
  }
  return void 0;
}
function dispatchRangeForSlot(portal, slotId, workspaceRoot) {
  const raw = portal.spec_map[slotId];
  if (!raw) {
    return void 0;
  }
  const parsed = parseSpecMapEntry(raw);
  if (!parsed) {
    return void 0;
  }
  const mod = portal.module_name || "unknown";
  const runtimePath = path3.join(workspaceRoot, ".semiformal", "runtime", `${mod}.semi.py`);
  return {
    uriPath: runtimePath,
    startLine1: parsed.startLine,
    endLine1: parsed.endLine
  };
}

// src/features/splitEditor/linkedHighlight.ts
var LinkedHighlightCoordinator = class {
  constructor(fadeMs) {
    this.fadeMs = fadeMs;
    this.highlight = import_vscode6.window.createTextEditorDecorationType({
      backgroundColor: new import_vscode6.ThemeColor("editor.wordHighlightBackground"),
      isWholeLine: false
    });
  }
  highlight;
  fadeTimer;
  dispose() {
    if (this.fadeTimer) {
      clearTimeout(this.fadeTimer);
    }
    this.highlight.dispose();
  }
  onSelectionOrPortal(editor, portal, workspaceRoot) {
    if (this.fadeTimer) {
      clearTimeout(this.fadeTimer);
      this.fadeTimer = void 0;
    }
    for (const ed of import_vscode6.window.visibleTextEditors) {
      ed.setDecorations(this.highlight, []);
    }
    if (!editor || !portal || !workspaceRoot) {
      return;
    }
    const doc = editor.document;
    const docPath = doc.uri.fsPath;
    const sel = editor.selection.active;
    const line1 = sel.line + 1;
    const dispatchPath = path4.join(
      workspaceRoot,
      ".semiformal",
      "runtime",
      `${portal.module_name}.semi.py`
    );
    if (pathsEqual(docPath, dispatchPath) || doc.uri.fsPath.endsWith(".semi.py")) {
      this.highlightDispatchToSource(editor, portal, workspaceRoot);
      return;
    }
    const slot = findSlotForSourceLine(portal, docPath, line1);
    if (!slot) {
      return;
    }
    const dr = dispatchRangeForSlot(portal, slot.slot_id, workspaceRoot);
    if (!dr) {
      return;
    }
    const targetUri = import_vscode6.Uri.file(dr.uriPath);
    const dispEd = import_vscode6.window.visibleTextEditors.find(
      (e) => e.document.uri.toString() === targetUri.toString()
    );
    if (!dispEd) {
      return;
    }
    const start = Math.max(1, dr.startLine1) - 1;
    const end = Math.max(1, dr.endLine1) - 1;
    const ranges = [];
    for (let i = start; i <= end; i++) {
      if (i < dispEd.document.lineCount) {
        ranges.push(dispEd.document.lineAt(i).range);
      }
    }
    dispEd.setDecorations(this.highlight, ranges);
    this.scheduleFade();
  }
  highlightDispatchToSource(editor, portal, workspaceRoot) {
    const line1 = editor.selection.active.line + 1;
    for (const slot of Object.values(portal.slots)) {
      const dr = dispatchRangeForSlot(portal, slot.slot_id, workspaceRoot);
      if (!dr) {
        continue;
      }
      if (!pathsEqual(dr.uriPath, editor.document.uri.fsPath)) {
        continue;
      }
      if (line1 >= dr.startLine1 && line1 <= dr.endLine1) {
        const sp = slot.slot_spec?.source_span;
        if (!sp || sp.length < 3) {
          return;
        }
        const [srcFile, a, b] = sp;
        const srcUri = import_vscode6.Uri.file(srcFile);
        const srcEd = import_vscode6.window.visibleTextEditors.find(
          (e) => e.document.uri.toString() === srcUri.toString()
        );
        if (!srcEd) {
          return;
        }
        const ranges = [];
        for (let i = a - 1; i <= b - 1; i++) {
          if (i < srcEd.document.lineCount) {
            ranges.push(srcEd.document.lineAt(i).range);
          }
        }
        srcEd.setDecorations(this.highlight, ranges);
        this.scheduleFade();
        return;
      }
    }
  }
  scheduleFade() {
    const ms = this.fadeMs();
    this.fadeTimer = setTimeout(() => {
      this.fadeTimer = void 0;
      for (const ed of import_vscode6.window.visibleTextEditors) {
        ed.setDecorations(this.highlight, []);
      }
    }, ms);
  }
};

// src/features/splitEditor/splitEditorCommand.ts
var path5 = __toESM(require("path"));
var import_vscode7 = require("vscode");
async function openDispatchSplitView(workspaceRoot, moduleName) {
  const rel = path5.join(".semiformal", "runtime", `${moduleName}.semi.py`);
  const abs = path5.join(workspaceRoot, rel);
  const uri = import_vscode7.Uri.file(abs);
  try {
    const doc = await import_vscode7.workspace.openTextDocument(uri);
    await import_vscode7.window.showTextDocument(doc, {
      viewColumn: import_vscode7.ViewColumn.Beside,
      preserveFocus: false
    });
  } catch {
    void import_vscode7.window.showErrorMessage(`Semipy: could not open dispatch file: ${abs}`);
  }
}

// src/features/versionTree/slotHistoryProvider.ts
var import_vscode9 = require("vscode");

// src/features/versionTree/walkHistory.ts
function walkHistoryCommits(slot, commitId) {
  const result = [];
  const seen = /* @__PURE__ */ new Set();
  const stack = [commitId];
  while (stack.length) {
    const cid = stack.pop();
    if (seen.has(cid)) {
      continue;
    }
    seen.add(cid);
    const c = slot.commits[cid];
    if (!c) {
      continue;
    }
    result.push(c);
    for (const pid of c.parent_ids) {
      stack.push(pid);
    }
  }
  return result;
}

// src/features/versionTree/slotTreeItems.ts
var import_vscode8 = require("vscode");
function decisionIcon(decision) {
  const d = (decision || "").toUpperCase();
  if (d === "GENERATE") {
    return new import_vscode8.ThemeIcon("git-commit");
  }
  if (d === "ADAPT") {
    return new import_vscode8.ThemeIcon("git-merge");
  }
  if (d === "REUSE" || d === "reuse") {
    return new import_vscode8.ThemeIcon("link");
  }
  if (d === "INSTANTIATE" || d === "instantiate") {
    return new import_vscode8.ThemeIcon("puzzle");
  }
  return new import_vscode8.ThemeIcon("git-commit");
}
function formatCommitLabel(c) {
  const id = c.commit_id.slice(0, 8);
  const msg = (c.message || "").replace(/\s+/g, " ").slice(0, 48);
  const ts = c.timestamp ? new Date(c.timestamp * 1e3).toLocaleString() : "";
  return `${id} | ${c.decision || "?"} | ${msg}${ts ? ` | ${ts}` : ""}`;
}
function truncateSpecPreview(spec, n = 60) {
  const t = spec.replace(/\s+/g, " ").trim();
  if (t.length <= n) {
    return t;
  }
  return t.slice(0, n - 1) + "\u2026";
}

// src/features/versionTree/slotHistoryProvider.ts
var SlotHistoryProvider = class {
  constructor(getPortal) {
    this.getPortal = getPortal;
  }
  _onDidChange = new import_vscode9.EventEmitter();
  onDidChangeTreeData = this._onDidChange.event;
  refresh() {
    this._onDidChange.fire(void 0);
  }
  getTreeItem(element) {
    if (element.kind === "portal") {
      const ti2 = new import_vscode9.TreeItem(
        element.portal.source_file || element.portal.module_name || "portal",
        import_vscode9.TreeItemCollapsibleState.Expanded
      );
      ti2.description = element.portal.module_name;
      return ti2;
    }
    if (element.kind === "slot") {
      const spec = element.slot.slot_spec?.spec_text || "";
      const ti2 = new import_vscode9.TreeItem(
        truncateSpecPreview(spec) || element.slot.slot_id.slice(0, 8),
        import_vscode9.TreeItemCollapsibleState.Expanded
      );
      ti2.description = element.slot.slot_id.slice(0, 8);
      return ti2;
    }
    if (element.kind === "branch") {
      const isDefault = element.branch.name === (element.slot.default_branch || "main");
      const ti2 = new import_vscode9.TreeItem(
        `${element.branch.name}${isDefault ? " (HEAD)" : ""}`,
        import_vscode9.TreeItemCollapsibleState.Expanded
      );
      return ti2;
    }
    const ti = new import_vscode9.TreeItem(
      formatCommitLabel(element.commit),
      import_vscode9.TreeItemCollapsibleState.None
    );
    ti.iconPath = decisionIcon(element.commit.decision);
    ti.contextValue = "semipy.commit";
    ti.command = {
      command: "semipy.viewGeneratedCode",
      title: "View generated code",
      arguments: [element.slot.slot_id, element.commit.commit_id]
    };
    return ti;
  }
  getChildren(element) {
    const portal = this.getPortal();
    if (!portal) {
      return [];
    }
    if (!element) {
      return [{ kind: "portal", portal }];
    }
    if (element.kind === "portal") {
      return Object.values(element.portal.slots).map((slot) => ({
        kind: "slot",
        portal: element.portal,
        slot
      }));
    }
    if (element.kind === "slot") {
      return Object.values(element.slot.branches).map((branch) => ({
        kind: "branch",
        portal: element.portal,
        slot: element.slot,
        branch
      }));
    }
    if (element.kind === "branch") {
      const headId = element.branch.head;
      const chain = walkHistoryCommits(element.slot, headId);
      return chain.map((commit) => ({
        kind: "commit",
        portal: element.portal,
        slot: element.slot,
        branchName: element.branch.name,
        commit
      }));
    }
    return [];
  }
  getParent(element) {
    return void 0;
  }
};

// src/features/versionTree/versionActions.ts
var import_child_process = require("child_process");
var import_vscode10 = require("vscode");
var previewSources = /* @__PURE__ */ new Map();
function setCommitPreviewSource(slotId, commitId, source) {
  const key = `${slotId}:${commitId}`;
  previewSources.set(key, source);
  return import_vscode10.Uri.from({ scheme: "semipy-commit", path: "/preview.py", query: key });
}
function registerCommitTextProvider() {
  return import_vscode10.workspace.registerTextDocumentContentProvider("semipy-commit", {
    provideTextDocumentContent(uri) {
      const key = uri.query;
      return previewSources.get(key) || "# Preview expired; run the tree command again.\n";
    }
  });
}
async function viewGeneratedCode(slotId, commitId, source) {
  const uri = setCommitPreviewSource(slotId, commitId, source);
  const doc = await import_vscode10.workspace.openTextDocument(uri);
  await import_vscode10.window.showTextDocument(doc, { preview: true });
}
function runSemipyCli(args, cwd) {
  return new Promise((resolve2) => {
    (0, import_child_process.execFile)("python3", ["-m", "semipy", ...args], { cwd }, (error, stdout, stderr) => {
      let code = 0;
      if (error) {
        const c = error.code;
        code = typeof c === "number" ? c : 1;
      }
      resolve2({
        stdout: String(stdout),
        stderr: String(stderr),
        code
      });
    });
  });
}

// src/features/diagnostics/diagnosticProvider.ts
var fs3 = __toESM(require("fs"));
var path6 = __toESM(require("path"));
var import_vscode11 = require("vscode");
var SemipyDiagnosticManager = class {
  constructor(semiformalRoot) {
    this.semiformalRoot = semiformalRoot;
  }
  collection = import_vscode11.languages.createDiagnosticCollection("semipy");
  dispose() {
    this.collection.dispose();
  }
  refresh() {
    this.collection.clear();
    const root = this.semiformalRoot();
    if (!root) {
      return;
    }
    const p = path6.join(root, ".semiformal", "diagnostics.json");
    let data;
    try {
      const raw = fs3.readFileSync(p, "utf8");
      data = JSON.parse(raw);
    } catch {
      return;
    }
    const byFile = /* @__PURE__ */ new Map();
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
      const uri = import_vscode11.Uri.file(fp);
      this.collection.set(uri, diags);
    }
  }
  entryToDiagnostic(e) {
    const start = Math.max(1, e.source_line_start) - 1;
    const end = Math.max(1, e.source_line_end) - 1;
    const sev = e.severity === "error" ? import_vscode11.DiagnosticSeverity.Error : e.severity === "warning" ? import_vscode11.DiagnosticSeverity.Warning : import_vscode11.DiagnosticSeverity.Information;
    const d = new import_vscode11.Diagnostic(
      new import_vscode11.Range(new import_vscode11.Position(start, 0), new import_vscode11.Position(end, 2e3)),
      e.message,
      sev
    );
    d.source = "semipy";
    d.code = e.slot_id ? `semi-call-error:${e.slot_id}` : e.code || "semi-call-error";
    d.relatedInformation = [];
    if (e.generated_path && e.generated_line_range?.length === 2) {
      const [a, b] = e.generated_line_range;
      const root = this.semiformalRoot() || "";
      const gp = path6.isAbsolute(e.generated_path) ? e.generated_path : path6.join(root, e.generated_path);
      d.relatedInformation.push({
        location: {
          uri: import_vscode11.Uri.file(gp),
          range: new import_vscode11.Range(
            new import_vscode11.Position(Math.max(1, a) - 1, 0),
            new import_vscode11.Position(Math.max(1, b) - 1, 2e3)
          )
        },
        message: "Generated implementation"
      });
    }
    return d;
  }
};

// src/features/diagnostics/codeActions.ts
var import_vscode12 = require("vscode");
function slotIdFromDiagnosticCode(code) {
  const s = typeof code === "object" && code !== null ? String(code.value) : String(code ?? "");
  if (s.startsWith("semi-call-error:")) {
    return s.slice("semi-call-error:".length);
  }
  return void 0;
}
function createRegenerateCodeActionProvider(getWorkspaceRoot, getPortalRelPath) {
  return {
    provideCodeActions(_document, _range, context) {
      const ws = getWorkspaceRoot();
      const portal = getPortalRelPath();
      if (!ws || !portal) {
        return [];
      }
      const hit = context.diagnostics.filter(
        (d) => String(d.source || "").includes("semipy")
      );
      if (!hit.length) {
        return [];
      }
      const slotId = slotIdFromDiagnosticCode(hit[0].code);
      if (!slotId) {
        return [];
      }
      const action = new import_vscode12.CodeAction("Regenerate this spec (semipy CLI)", import_vscode12.CodeActionKind.QuickFix);
      action.command = {
        command: "semipy.regenerateSlotDiagnostic",
        title: "Regenerate",
        arguments: [ws, portal, slotId]
      };
      return [action];
    }
  };
}

// src/extension.ts
function refreshPortalForUri(fsPath, state) {
  state.workspaceRoot = findWorkspaceRootContainingSemiformal(fsPath);
  if (!state.workspaceRoot) {
    state.portal = void 0;
    state.portalPath = void 0;
    return;
  }
  const expected = expectedPortalJsonPath(fsPath);
  if (!expected) {
    state.portal = void 0;
    state.portalPath = void 0;
    return;
  }
  state.portalPath = expected;
  state.portal = loadPortalJson(expected);
  if (state.portal && !portalMatchesEditorFile(state.portal, fsPath)) {
    state.portal = void 0;
  }
}
function activate(context) {
  const portalState = {
    portal: void 0,
    portalPath: void 0,
    workspaceRoot: void 0
  };
  const opacityTypes = createOpacityDecorationTypes();
  const phraseTypes = createPhraseDecorationTypes();
  const debounceMs = () => import_vscode13.workspace.getConfiguration("semipy").get("debounceMs") ?? 200;
  const signFlip = new SignFlipCoordinator(
    () => import_vscode13.workspace.getConfiguration("semipy").get("signFlipOnSkeletonEdit") ?? true
  );
  const linked = new LinkedHighlightCoordinator(
    () => import_vscode13.workspace.getConfiguration("semipy").get("linkedHighlightFadeMs") ?? 1500
  );
  const diag = new SemipyDiagnosticManager(() => portalState.workspaceRoot);
  const tree = new SlotHistoryProvider(() => portalState.portal);
  const treeView = import_vscode13.window.createTreeView("semipy.slotHistory", {
    treeDataProvider: tree,
    showCollapseAll: true
  });
  const status = import_vscode13.window.createStatusBarItem(import_vscode13.StatusBarAlignment.Left, 100);
  status.command = "semipy.refreshHistory";
  function refreshAllDecorations(editor) {
    if (!editor) {
      return;
    }
    refreshPortalForUri(editor.document.uri.fsPath, portalState);
    const root = portalState.workspaceRoot;
    refreshOpacityDecorations(editor, opacityTypes.reasoningDim);
    refreshPhraseDecorations(editor, portalState.portal, root, phraseTypes);
    const n = portalState.portal ? Object.keys(portalState.portal.slots).length : 0;
    status.text = `Semipy: ${n} slot(s)`;
    status.show();
    tree.refresh();
    diag.refresh();
    linked.onSelectionOrPortal(editor, portalState.portal, root);
  }
  const opacitySub = subscribeOpacityWrapper(opacityTypes, debounceMs, refreshAllDecorations);
  context.subscriptions.push(
    treeView,
    status,
    opacitySub,
    signFlip.attach(),
    { dispose: () => linked.dispose() },
    diag,
    import_vscode13.window.onDidChangeTextEditorSelection((e) => {
      refreshPortalForUri(e.textEditor.document.uri.fsPath, portalState);
      linked.onSelectionOrPortal(e.textEditor, portalState.portal, portalState.workspaceRoot);
    }),
    import_vscode13.window.onDidChangeActiveTextEditor((ed) => {
      if (ed) {
        signFlip.seedDocument(ed.document);
      }
      refreshAllDecorations(ed);
    }),
    import_vscode13.languages.registerHoverProvider(
      { language: "python", scheme: "file" },
      createPhraseHoverProvider(
        () => portalState.portal,
        () => portalState.workspaceRoot
      )
    ),
    registerCommitTextProvider(),
    import_vscode13.commands.registerCommand("semipy.openSplitView", async () => {
      const ed = import_vscode13.window.activeTextEditor;
      if (!ed) {
        return;
      }
      refreshPortalForUri(ed.document.uri.fsPath, portalState);
      if (!portalState.portal || !portalState.workspaceRoot) {
        void import_vscode13.window.showWarningMessage("Semipy: no portal for this file.");
        return;
      }
      await openDispatchSplitView(portalState.workspaceRoot, portalState.portal.module_name);
    }),
    import_vscode13.commands.registerCommand("semipy.refreshHistory", () => {
      const ed = import_vscode13.window.activeTextEditor;
      refreshAllDecorations(ed);
      tree.refresh();
    }),
    import_vscode13.commands.registerCommand(
      "semipy.viewGeneratedCode",
      async (slotId, commitId) => {
        const fromDisk = portalState.portalPath !== void 0 ? loadPortalJson(portalState.portalPath) : void 0;
        const portal = fromDisk ?? portalState.portal;
        const slot = portal?.slots[slotId];
        const src = slot?.commits[commitId]?.generated_source;
        if (!src) {
          void import_vscode13.window.showWarningMessage("Semipy: commit source not loaded; refresh history.");
          return;
        }
        await viewGeneratedCode(slotId, commitId, src);
      }
    ),
    import_vscode13.commands.registerCommand(
      "semipy.regenerateSlotDiagnostic",
      async (ws, portalRel, slotId) => {
        const r = await runSemipyCli(
          ["regenerate", "--portal", portalRel, "--slot-id", slotId],
          ws
        );
        void import_vscode13.window.showInformationMessage(r.stderr || r.stdout || "semipy regenerate finished.");
        diag.refresh();
      }
    ),
    import_vscode13.languages.registerCodeActionsProvider(
      { language: "python", scheme: "file" },
      createRegenerateCodeActionProvider(
        () => portalState.workspaceRoot,
        () => portalState.portalPath && portalState.workspaceRoot ? path7.relative(portalState.workspaceRoot, portalState.portalPath) : void 0
      )
    )
  );
  const ed0 = import_vscode13.window.activeTextEditor;
  if (ed0) {
    signFlip.seedDocument(ed0.document);
  }
  refreshAllDecorations(ed0);
  if (import_vscode13.workspace.workspaceFolders?.length) {
    const wf = import_vscode13.workspace.workspaceFolders[0].uri.fsPath;
    const w = import_vscode13.workspace.createFileSystemWatcher(new import_vscode13.RelativePattern(wf, ".semiformal/**/*"));
    let timer;
    const fire = () => {
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(() => {
        timer = void 0;
        refreshAllDecorations(import_vscode13.window.activeTextEditor);
      }, debounceMs());
    };
    w.onDidChange(fire);
    w.onDidCreate(fire);
    w.onDidDelete(fire);
    context.subscriptions.push(w);
  }
}
function subscribeOpacityWrapper(types, debounceMs, onRefresh) {
  let timer;
  const tick = () => {
    onRefresh(import_vscode13.window.activeTextEditor);
  };
  const sub1 = import_vscode13.window.onDidChangeActiveTextEditor(() => {
    tick();
  });
  const sub2 = import_vscode13.workspace.onDidChangeTextDocument((ev) => {
    const ed = import_vscode13.window.activeTextEditor;
    if (!ed || ev.document !== ed.document) {
      return;
    }
    if (timer) {
      clearTimeout(timer);
    }
    timer = setTimeout(() => {
      timer = void 0;
      tick();
    }, debounceMs());
  });
  tick();
  return {
    dispose: () => {
      sub1.dispose();
      sub2.dispose();
      if (timer) {
        clearTimeout(timer);
      }
      types.reasoningDim.dispose();
    }
  };
}
function deactivate() {
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  activate,
  deactivate
});
//# sourceMappingURL=extension.js.map
