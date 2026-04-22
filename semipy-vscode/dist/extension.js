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
var path8 = __toESM(require("path"));
var import_vscode16 = require("vscode");

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
function pathsEqualRobust(a, b) {
  try {
    const na = path.normalize(a);
    const nb = path.normalize(b);
    if (na === nb) {
      return true;
    }
    if (na.toLowerCase() === nb.toLowerCase()) {
      return true;
    }
    try {
      const ra = fs.realpathSync(na);
      const rb = fs.realpathSync(nb);
      if (ra === rb || ra.toLowerCase() === rb.toLowerCase()) {
        return true;
      }
    } catch {
    }
  } catch {
    return false;
  }
  return false;
}
function portalReferencesEditorPath(portal, editorFsPath) {
  try {
    for (const slot of Object.values(portal.slots)) {
      const spec = slot.slot_spec;
      if (!spec || typeof spec !== "object") {
        continue;
      }
      const src = spec.source_span;
      if (Array.isArray(src) && typeof src[0] === "string") {
        if (pathsEqualRobust(src[0], editorFsPath)) {
          return true;
        }
      }
      const enc = spec.enclosing_function_span;
      if (Array.isArray(enc) && typeof enc[0] === "string") {
        if (pathsEqualRobust(enc[0], editorFsPath)) {
          return true;
        }
      }
    }
  } catch {
    return false;
  }
  return false;
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
function isDirectory(p) {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}
function portalMatchesEditorFile(portal, editorFsPath) {
  if (portalReferencesEditorPath(portal, editorFsPath)) {
    return true;
  }
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
    const normEditor = path.normalize(editorFsPath);
    const normSf = path.normalize(sf);
    if (pathsEqualRobust(normSf, normEditor)) {
      return true;
    }
    if (path.basename(normSf).toLowerCase() === path.basename(normEditor).toLowerCase()) {
      return true;
    }
    if (isDirectory(normSf)) {
      const withSep = normSf.endsWith(path.sep) ? normSf : normSf + path.sep;
      if (normEditor === normSf || normEditor.startsWith(withSep)) {
        return true;
      }
    }
  } catch {
    return false;
  }
  return false;
}
function collectPortalCacheDirsNearSource(sourceFilePath) {
  const out = [];
  const seen = /* @__PURE__ */ new Set();
  const push = (p) => {
    try {
      const n = path.resolve(p);
      if (!seen.has(n)) {
        seen.add(n);
        out.push(n);
      }
    } catch {
    }
  };
  let dir = path.dirname(path.resolve(sourceFilePath));
  const { root } = path.parse(sourceFilePath);
  while (true) {
    try {
      const names = fs.readdirSync(dir);
      if (names.some((f) => f.endsWith(".portal.json"))) {
        push(dir);
      }
    } catch {
    }
    const legacy = path.join(dir, ".semiformal");
    try {
      if (fs.existsSync(legacy) && fs.statSync(legacy).isDirectory()) {
        push(legacy);
      }
    } catch {
    }
    try {
      for (const name of fs.readdirSync(dir)) {
        if (!name.startsWith(".semiformal") || name === ".semiformal") {
          continue;
        }
        const sub = path.join(dir, name);
        try {
          if (fs.statSync(sub).isDirectory()) {
            push(sub);
          }
        } catch {
        }
      }
    } catch {
    }
    if (dir === root) {
      break;
    }
    dir = path.dirname(dir);
  }
  return out;
}
function findPortalJsonPathForEditor(sourceFilePath, opts) {
  const resolved = path.resolve(sourceFilePath);
  const parentDir = path.dirname(resolved);
  const cacheDirs = collectPortalCacheDirsNearSource(sourceFilePath);
  if (!cacheDirs.length) {
    return void 0;
  }
  const sessionIds = [];
  const pushId = (compute) => {
    try {
      const s = compute();
      if (s && !sessionIds.includes(s)) {
        sessionIds.push(s);
      }
    } catch {
    }
  };
  const fromSettings = (opts?.sessionSourceFromSettings || "").trim();
  if (fromSettings) {
    pushId(() => sessionIdFromFilename(path.resolve(fromSettings)));
  }
  const env = (process.env.SEMIPY_SESSION_SOURCE || "").trim();
  if (env) {
    pushId(() => sessionIdFromFilename(path.resolve(env)));
  }
  pushId(() => sessionIdFromFilename(resolvePortalAnchorSourcePath(sourceFilePath)));
  pushId(() => sessionIdFromFilename(parentDir));
  pushId(() => sessionIdFromFilename(resolved));
  const candidates = [];
  const push = (p) => {
    if (!candidates.includes(p)) {
      candidates.push(p);
    }
  };
  for (const cacheDir of cacheDirs) {
    for (const sid of sessionIds) {
      try {
        push(path.join(cacheDir, `${sid}.portal.json`));
      } catch {
      }
    }
  }
  for (const c of candidates) {
    try {
      if (fs.existsSync(c)) {
        const raw = fs.readFileSync(c, "utf8");
        const portal = JSON.parse(raw);
        if (portalMatchesEditorFile(portal, sourceFilePath)) {
          return c;
        }
      }
    } catch {
    }
  }
  for (const cacheDir of cacheDirs) {
    try {
      const files = fs.readdirSync(cacheDir).filter((f) => f.endsWith(".portal.json"));
      for (const f of files) {
        const full = path.join(cacheDir, f);
        try {
          const raw = fs.readFileSync(full, "utf8");
          const portal = JSON.parse(raw);
          if (portalMatchesEditorFile(portal, sourceFilePath)) {
            return full;
          }
        } catch {
        }
      }
    } catch {
    }
  }
  return void 0;
}
function loadPortalJson(portalPath) {
  try {
    const raw = fs.readFileSync(portalPath, "utf8");
    return JSON.parse(raw);
  } catch {
    return void 0;
  }
}

// src/features/commentOpacity/opacityDecorations.ts
var import_vscode = require("vscode");

// src/util/hashArrowDetect.ts
function isReasoningLine(line) {
  const stripped = line.replace(/^\s+/, "");
  return stripped.startsWith("#<") || stripped.startsWith("# <");
}
function hashArrowSpecSuffixFromLine(line) {
  const m = /#\s*>/.exec(line);
  if (!m || m.index === void 0) {
    return null;
  }
  const baseCol = m.index + m[0].length;
  return { baseCol, suffix: line.slice(baseCol) };
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

// node_modules/diff/lib/index.mjs
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
  addToPath: function addToPath(path9, added, removed, oldPosInc) {
    var last = path9.lastComponent;
    if (last && last.added === added && last.removed === removed) {
      return {
        oldPos: path9.oldPos + oldPosInc,
        lastComponent: {
          count: last.count + 1,
          added,
          removed,
          previousComponent: last.previousComponent
        }
      };
    } else {
      return {
        oldPos: path9.oldPos + oldPosInc,
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
function shouldSkipSignFlipForBulkEdit(e, before, after) {
  const oldL = before.split(/\r?\n/).length;
  const newL = after.split(/\r?\n/).length;
  if (Math.abs(newL - oldL) > 4) {
    return true;
  }
  let total = 0;
  let maxNl = 0;
  for (const c of e.contentChanges) {
    total += c.text.length;
    maxNl = Math.max(maxNl, (c.text.match(/\n/g) || []).length);
  }
  if (total > 500) {
    return true;
  }
  if (maxNl >= 4) {
    return true;
  }
  if (e.contentChanges.length > 12) {
    return true;
  }
  return false;
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
  constructor(enabled, skipApiEdits) {
    this.enabled = enabled;
    this.skipApiEdits = skipApiEdits;
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
    const reasonApi = 3;
    if (this.skipApiEdits() && e.reason === reasonApi) {
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
    if (before === void 0 || before === after) {
      this.previousText.set(uriKey, after);
      return;
    }
    if (shouldSkipSignFlipForBulkEdit(e, before, after)) {
      this.previousText.set(uriKey, after);
      return;
    }
    this.previousText.set(uriKey, after);
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
function sketchLibraryPath(cacheDir) {
  return path2.join(cacheDir, "sketch_library.json");
}
function loadSketchLibraryFile(p) {
  try {
    const raw = fs2.readFileSync(p, "utf8");
    return JSON.parse(raw);
  } catch {
    return void 0;
  }
}
function loadSketchLibraryMerged(cacheDir, workspaceRoots) {
  const merged = { version: 1, sketches: {}, bindings: {} };
  let any = false;
  const absorb = (lib) => {
    if (!lib) {
      return;
    }
    any = true;
    Object.assign(merged.bindings ||= {}, lib.bindings || {});
    Object.assign(merged.sketches ||= {}, lib.sketches || {});
    if (lib.version !== void 0) {
      merged.version = lib.version;
    }
  };
  absorb(loadSketchLibraryFile(sketchLibraryPath(cacheDir)));
  const scanDir = (dir) => {
    let names;
    try {
      names = fs2.readdirSync(dir);
    } catch {
      return;
    }
    for (const name of names) {
      if (!name.startsWith(".semiformal")) {
        continue;
      }
      const sub = path2.join(dir, name);
      let isDir = false;
      try {
        isDir = fs2.statSync(sub).isDirectory();
      } catch {
        continue;
      }
      if (!isDir) {
        continue;
      }
      absorb(loadSketchLibraryFile(path2.join(sub, "sketch_library.json")));
    }
  };
  for (const root of workspaceRoots ?? []) {
    scanDir(root);
    let entries;
    try {
      entries = fs2.readdirSync(root, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const ent of entries) {
      if (!ent.isDirectory() || ent.name.startsWith(".")) {
        continue;
      }
      scanDir(path2.join(root, ent.name));
    }
  }
  return any ? merged : void 0;
}
function bindingById(lib, bindingId) {
  if (!lib?.bindings || !bindingId) {
    return void 0;
  }
  return lib.bindings[bindingId];
}
function resolveBindingIdForCommit(lib, commitId, explicitBindingId) {
  const e = (explicitBindingId || "").trim();
  if (e) {
    return e;
  }
  if (!lib?.sketches || !commitId) {
    return void 0;
  }
  for (const sk of Object.values(lib.sketches)) {
    if (!sk || typeof sk !== "object") {
      continue;
    }
    const ids = sk.source_commit_ids || [];
    if (!ids.includes(commitId)) {
      continue;
    }
    const bid = (sk.binding_id || "").trim();
    if (bid) {
      return bid;
    }
  }
  return void 0;
}

// src/features/splitEditor/portalCommit.ts
var LOCK_REF = "__locked__";
function activeCommitFromPortalSlot(slot) {
  const locked = slot.refs?.[LOCK_REF];
  if (locked && slot.commits[locked]) {
    return slot.commits[locked];
  }
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
  const ids = new Set(
    Object.entries(slot.refs).filter(([k]) => k !== LOCK_REF).map(([, v]) => v)
  );
  const candidates = [...ids].map((id) => slot.commits[id]).filter(Boolean);
  if (candidates.length === 0) {
    return void 0;
  }
  return candidates.reduce((a, b) => a.timestamp >= b.timestamp ? a : b);
}

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
  return pathsEqualRobust(a, b);
}
function findSlotForSourceLine(portal, sourceFsPath, line1, fullText) {
  for (const slot of Object.values(portal.slots)) {
    const sp = slot.slot_spec;
    const span = sp?.source_span;
    if (!span || span.length < 3) {
      continue;
    }
    const [fn] = span;
    if (!pathsEqualRobust(fn, sourceFsPath)) {
      continue;
    }
    const block = resolveSourceBlockRange(fullText, slot);
    if (!block) {
      continue;
    }
    if (line1 >= block.startLine1 && line1 <= block.endLine1) {
      return slot;
    }
  }
  return void 0;
}
function offsetOfLine1(fullText, line1) {
  if (line1 <= 1) {
    return 0;
  }
  let pos = 0;
  for (let k = 1; k < line1; k++) {
    const nl = fullText.indexOf("\n", pos);
    if (nl < 0) {
      return pos;
    }
    pos = nl + 1;
  }
  return pos;
}
function resolveSourceBlockRange(fullText, slot) {
  const sp = slot.slot_spec;
  if (!sp?.source_span || sp.source_span.length < 3) {
    return void 0;
  }
  const [, start, end] = sp.source_span;
  const lines = fullText.split(/\r?\n/);
  const slice = lines.slice(start - 1, end).join("\n");
  const specText = (sp.spec_text || "").trim();
  if (specText && slice.trim() === specText) {
    return { startLine1: start, endLine1: end };
  }
  if (specText) {
    const staleCharHint = offsetOfLine1(fullText, start);
    let idx = fullText.indexOf(specText, Math.max(0, staleCharHint - 400));
    if (idx < 0) {
      idx = fullText.indexOf(specText);
    }
    if (idx >= 0) {
      const before = fullText.slice(0, idx);
      const startLine1 = before.split(/\r?\n/).length;
      const spanLines = specText.split(/\r?\n/).length;
      return { startLine1, endLine1: startLine1 + spanLines - 1 };
    }
  }
  return { startLine1: start, endLine1: end };
}
function dispatchRangeForSlot(portal, slotId, portalCacheDir) {
  const raw = portal.spec_map[slotId];
  if (!raw) {
    return void 0;
  }
  const parsed = parseSpecMapEntry(raw);
  if (!parsed) {
    return void 0;
  }
  const mod = portal.module_name || "unknown";
  const runtimePath = path3.join(portalCacheDir, "runtime", `${mod}.semi.py`);
  return {
    uriPath: runtimePath,
    startLine1: parsed.startLine,
    endLine1: parsed.endLine
  };
}

// src/features/phraseHighlight/phraseHoverProvider.ts
function createPhraseHoverProvider(getPortal, getPortalCacheDir, getWorkspaceRoots) {
  return {
    provideHover(document, pos) {
      if (document.languageId !== "python") {
        return void 0;
      }
      const portal = getPortal();
      const cacheDir = getPortalCacheDir();
      if (!portal || !cacheDir) {
        return void 0;
      }
      const line1 = pos.line + 1;
      const fullText = document.getText();
      const lineText = document.lineAt(pos.line).text;
      const specRegion = hashArrowSpecSuffixFromLine(lineText);
      if (!specRegion || pos.character < specRegion.baseCol) {
        return void 0;
      }
      const lib = loadSketchLibraryMerged(cacheDir, getWorkspaceRoots());
      const suffix = specRegion.suffix;
      const rel = pos.character - specRegion.baseCol;
      const lowerSuffix = suffix.toLowerCase();
      for (const slot of Object.values(portal.slots)) {
        const head = activeCommitFromPortalSlot(slot);
        const cid = head?.commit_id || "";
        const bid = resolveBindingIdForCommit(lib, cid, head?.binding_id) || "";
        if (!bid) {
          continue;
        }
        const binding = bindingById(lib, bid);
        if (!binding?.phrases?.length) {
          continue;
        }
        const block = resolveSourceBlockRange(fullText, slot);
        if (!block || line1 < block.startLine1 || line1 > block.endLine1) {
          continue;
        }
        const sorted = [...binding.phrases].sort((x, y) => y.text.length - x.text.length);
        for (const p of sorted) {
          const t = (p.text || "").trim();
          if (!t) {
            continue;
          }
          const tl = t.toLowerCase();
          let idx = 0;
          while (idx <= lowerSuffix.length) {
            const at = lowerSuffix.indexOf(tl, idx);
            if (at < 0) {
              break;
            }
            if (suffix.slice(at, at + t.length).toLowerCase() !== tl) {
              idx = at + 1;
              continue;
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
var import_vscode6 = require("vscode");

// src/logging/semipyOutputChannel.ts
var import_vscode5 = require("vscode");
var channel;
function getSemipyOutputChannel() {
  if (!channel) {
    channel = import_vscode5.window.createOutputChannel("Semipy");
  }
  return channel;
}
function appendSemipyLog(line) {
  getSemipyOutputChannel().appendLine(line);
}

// src/features/phraseHighlight/phraseDecorations.ts
var ROLE_ORDER = ["operation", "param", "operator", "connective"];
var ROLE_STYLES = {
  operation: {
    light: { color: "#00639c", backgroundColor: "rgba(0, 99, 156, 0)" },
    dark: { color: "#4ec9b0", backgroundColor: "rgba(78, 201, 176, 0)" }
  },
  param: {
    light: { color: "#a31515", backgroundColor: "rgba(163, 21, 21, 0)" },
    dark: { color: "#ce9178", backgroundColor: "rgba(206, 145, 120, 0)" }
  },
  operator: {
    light: { color: "#811f3f", backgroundColor: "rgba(129, 31, 63, 0)" },
    dark: { color: "#dcdcaa", backgroundColor: "rgba(220, 220, 170, 0)" }
  },
  connective: {
    light: { color: "#444444", backgroundColor: "rgba(68, 68, 68, 0)" },
    dark: { color: "#9cdcfe", backgroundColor: "rgba(156, 220, 254, 0)" }
  }
};
function createPhraseDecorationTypes() {
  const out = {};
  for (const role of ROLE_ORDER) {
    const st = ROLE_STYLES[role] ?? ROLE_STYLES.param;
    out[role] = import_vscode6.window.createTextEditorDecorationType({
      rangeBehavior: import_vscode6.DecorationRangeBehavior.ClosedClosed,
      light: { ...st.light, fontWeight: role === "operation" ? "600" : void 0 },
      dark: { ...st.dark, fontWeight: role === "operation" ? "600" : void 0 }
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
  const lowerSuffix = suffix.toLowerCase();
  for (const p of sorted) {
    const t = (p.text || "").trim();
    if (!t) {
      continue;
    }
    const tl = t.toLowerCase();
    let search = 0;
    while (search <= lowerSuffix.length) {
      const pos = lowerSuffix.indexOf(tl, search);
      if (pos < 0) {
        break;
      }
      const end = pos + t.length;
      if (suffix.slice(pos, end).toLowerCase() !== tl) {
        search = pos + 1;
        continue;
      }
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
function refreshPhraseDecorations(editor, portal, portalCacheDir, types, workspaceRoots) {
  for (const t of Object.values(types)) {
    editor.setDecorations(t, []);
  }
  if (!portal || !portalCacheDir) {
    return;
  }
  const lib = loadSketchLibraryMerged(portalCacheDir, workspaceRoots);
  const doc = editor.document;
  const full = doc.getText();
  const lines = full.split(/\r?\n/);
  const trace = import_vscode6.workspace.getConfiguration("semipy").get("tracePhraseDecorations") ?? false;
  if (trace) {
    appendSemipyLog(`[phrase] refresh ${doc.uri.fsPath}`);
    appendSemipyLog(
      `  cacheDir=${portalCacheDir} lib=${lib ? "ok" : "missing"} bindingKeys=${lib?.bindings ? Object.keys(lib.bindings).length : 0}`
    );
  }
  const rangesByRole = {};
  for (const r of ROLE_ORDER) {
    rangesByRole[r] = [];
  }
  for (const slot of Object.values(portal.slots)) {
    const head = activeCommitFromPortalSlot(slot);
    const cid = head?.commit_id || "";
    const bid = resolveBindingIdForCommit(lib, cid, head?.binding_id) || "";
    if (trace) {
      appendSemipyLog(
        `  slot ${slot.slot_id.slice(0, 8)} commit ${cid.slice(0, 12) || "?"} resolved binding_id=${bid || "none"}`
      );
    }
    if (!bid) {
      continue;
    }
    const binding = bindingById(lib, bid);
    if (!binding?.phrases?.length) {
      if (trace) {
        appendSemipyLog(`    no phrases for binding ${bid.slice(0, 8)}`);
      }
      continue;
    }
    const block = resolveSourceBlockRange(full, slot);
    if (!block) {
      if (trace) {
        appendSemipyLog(`    no source block range`);
      }
      continue;
    }
    const { startLine1: start1, endLine1: end1 } = block;
    let spanTotal = 0;
    for (let lineIdx = start1 - 1; lineIdx <= end1 - 1; lineIdx++) {
      if (lineIdx < 0 || lineIdx >= lines.length) {
        continue;
      }
      const line = lines[lineIdx];
      const specRegion = hashArrowSpecSuffixFromLine(line);
      if (!specRegion) {
        continue;
      }
      const suffix = specRegion.suffix;
      const spans = phraseSpansInSuffix(suffix, binding.phrases);
      spanTotal += spans.length;
      const lineObj = doc.lineAt(lineIdx);
      for (const sp of spans) {
        const role = ROLE_ORDER.includes(sp.role) ? sp.role : "param";
        const startCol = specRegion.baseCol + sp.start;
        const endCol = specRegion.baseCol + sp.end;
        const r = new import_vscode6.Range(
          new import_vscode6.Position(lineIdx, startCol),
          new import_vscode6.Position(lineIdx, Math.min(endCol, lineObj.text.length))
        );
        rangesByRole[role].push(r);
      }
    }
    if (trace) {
      appendSemipyLog(`    block lines ${start1}-${end1} phraseSpans=${spanTotal}`);
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
var import_vscode7 = require("vscode");
var LinkedHighlightCoordinator = class {
  constructor(fadeMs) {
    this.fadeMs = fadeMs;
    this.highlight = import_vscode7.window.createTextEditorDecorationType({
      backgroundColor: new import_vscode7.ThemeColor("editor.wordHighlightBackground"),
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
  onSelectionOrPortal(editor, portal, portalCacheDir) {
    if (this.fadeTimer) {
      clearTimeout(this.fadeTimer);
      this.fadeTimer = void 0;
    }
    for (const ed of import_vscode7.window.visibleTextEditors) {
      ed.setDecorations(this.highlight, []);
    }
    if (!editor || !portal || !portalCacheDir) {
      return;
    }
    const doc = editor.document;
    const docPath = doc.uri.fsPath;
    const sel = editor.selection.active;
    const line1 = sel.line + 1;
    const fullText = doc.getText();
    const dispatchPath = path4.join(portalCacheDir, "runtime", `${portal.module_name}.semi.py`);
    if (pathsEqual(docPath, dispatchPath) || doc.uri.fsPath.endsWith(".semi.py")) {
      this.highlightDispatchToSource(editor, portal, portalCacheDir);
      return;
    }
    const slot = findSlotForSourceLine(portal, docPath, line1, fullText);
    if (!slot) {
      return;
    }
    const dr = dispatchRangeForSlot(portal, slot.slot_id, portalCacheDir);
    if (!dr) {
      return;
    }
    const targetUri = import_vscode7.Uri.file(dr.uriPath);
    const dispEd = import_vscode7.window.visibleTextEditors.find(
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
  highlightDispatchToSource(editor, portal, portalCacheDir) {
    const line1 = editor.selection.active.line + 1;
    for (const slot of Object.values(portal.slots)) {
      const dr = dispatchRangeForSlot(portal, slot.slot_id, portalCacheDir);
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
        const [srcFile] = sp;
        const srcUri = import_vscode7.Uri.file(srcFile);
        const srcEd = import_vscode7.window.visibleTextEditors.find(
          (e) => e.document.uri.toString() === srcUri.toString()
        );
        if (!srcEd) {
          return;
        }
        const full = srcEd.document.getText();
        const block = resolveSourceBlockRange(full, slot);
        const ranges = [];
        const a = block?.startLine1 ?? sp[1];
        const b = block?.endLine1 ?? sp[2];
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
      for (const ed of import_vscode7.window.visibleTextEditors) {
        ed.setDecorations(this.highlight, []);
      }
    }, ms);
  }
};

// src/features/splitEditor/splitEditorCommand.ts
var path5 = __toESM(require("path"));
var import_vscode8 = require("vscode");
async function openDispatchSplitView(portalCacheDir, moduleName) {
  const abs = path5.join(portalCacheDir, "runtime", `${moduleName}.semi.py`);
  const uri = import_vscode8.Uri.file(abs);
  try {
    const doc = await import_vscode8.workspace.openTextDocument(uri);
    await import_vscode8.window.showTextDocument(doc, {
      viewColumn: import_vscode8.ViewColumn.Beside,
      preserveFocus: false
    });
  } catch {
    void import_vscode8.window.showErrorMessage(`Semipy: could not open dispatch file: ${abs}`);
  }
}

// src/features/versionTree/slotHistoryProvider.ts
var import_vscode10 = require("vscode");

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
var import_vscode9 = require("vscode");
function decisionIcon(decision) {
  const d = (decision || "").toUpperCase();
  if (d === "GENERATE") {
    return new import_vscode9.ThemeIcon("git-commit");
  }
  if (d === "ADAPT") {
    return new import_vscode9.ThemeIcon("git-merge");
  }
  if (d === "REUSE" || d === "reuse") {
    return new import_vscode9.ThemeIcon("link");
  }
  if (d === "INSTANTIATE" || d === "instantiate") {
    return new import_vscode9.ThemeIcon("puzzle");
  }
  return new import_vscode9.ThemeIcon("git-commit");
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
  _onDidChange = new import_vscode10.EventEmitter();
  onDidChangeTreeData = this._onDidChange.event;
  refresh() {
    this._onDidChange.fire(void 0);
  }
  getTreeItem(element) {
    if (element.kind === "portal") {
      const ti2 = new import_vscode10.TreeItem(
        element.portal.source_file || element.portal.module_name || "portal",
        import_vscode10.TreeItemCollapsibleState.Expanded
      );
      ti2.description = element.portal.module_name;
      return ti2;
    }
    if (element.kind === "slot") {
      const spec = element.slot.slot_spec?.spec_text || "";
      const ti2 = new import_vscode10.TreeItem(
        truncateSpecPreview(spec) || element.slot.slot_id.slice(0, 8),
        import_vscode10.TreeItemCollapsibleState.Expanded
      );
      ti2.description = element.slot.slot_id.slice(0, 8);
      return ti2;
    }
    if (element.kind === "branch") {
      const isDefault = element.branch.name === (element.slot.default_branch || "main");
      const ti2 = new import_vscode10.TreeItem(
        `${element.branch.name}${isDefault ? " (HEAD)" : ""}`,
        import_vscode10.TreeItemCollapsibleState.Expanded
      );
      return ti2;
    }
    const ti = new import_vscode10.TreeItem(
      formatCommitLabel(element.commit),
      import_vscode10.TreeItemCollapsibleState.None
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
var fs3 = __toESM(require("fs"));
var path6 = __toESM(require("path"));
var import_vscode11 = require("vscode");
var previewSources = /* @__PURE__ */ new Map();
function setCommitPreviewSource(slotId, commitId, source) {
  const key = `${slotId}:${commitId}`;
  previewSources.set(key, source);
  return import_vscode11.Uri.from({ scheme: "semipy-commit", path: "/preview.py", query: key });
}
function registerCommitTextProvider() {
  return import_vscode11.workspace.registerTextDocumentContentProvider("semipy-commit", {
    provideTextDocumentContent(uri) {
      const key = uri.query;
      return previewSources.get(key) || "# Preview expired; run the tree command again.\n";
    }
  });
}
async function viewGeneratedCode(slotId, commitId, source) {
  const uri = setCommitPreviewSource(slotId, commitId, source);
  const doc = await import_vscode11.workspace.openTextDocument(uri);
  await import_vscode11.window.showTextDocument(doc, { preview: true });
}
function expandWorkspaceVars(s) {
  let out = s;
  const folders = import_vscode11.workspace.workspaceFolders ?? [];
  if (out.includes("${workspaceFolder}")) {
    const first = folders[0]?.uri.fsPath;
    if (first) {
      out = out.replace(/\$\{workspaceFolder\}/g, first);
    }
  }
  return out;
}
function resolveConfiguredPythonPath(raw) {
  let out = expandWorkspaceVars(raw.trim());
  if (!out) {
    return out;
  }
  if (!path6.isAbsolute(out)) {
    const wf = import_vscode11.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (wf) {
      out = path6.resolve(wf, out);
    }
  }
  try {
    return path6.normalize(out);
  } catch {
    return out;
  }
}
function pushIfFile(out, p) {
  try {
    if (fs3.existsSync(p) && fs3.statSync(p).isFile()) {
      out.push(p);
    }
  } catch {
  }
}
function candidateVenvInterpreterPaths() {
  const candidates = [];
  const roots = /* @__PURE__ */ new Set();
  for (const wf of import_vscode11.workspace.workspaceFolders ?? []) {
    roots.add(wf.uri.fsPath);
    roots.add(path6.dirname(wf.uri.fsPath));
  }
  for (const root of roots) {
    pushIfFile(candidates, path6.join(root, ".venv", "bin", "python"));
    pushIfFile(candidates, path6.join(root, ".venv", "bin", "python3"));
    pushIfFile(candidates, path6.join(root, "venv", "bin", "python"));
    pushIfFile(candidates, path6.join(root, "venv", "bin", "python3"));
    pushIfFile(candidates, path6.join(root, ".venv", "Scripts", "python.exe"));
    pushIfFile(candidates, path6.join(root, "venv", "Scripts", "python.exe"));
  }
  return candidates;
}
function resolvePythonExecutable() {
  const cfg = import_vscode11.workspace.getConfiguration("semipy");
  const explicit = resolveConfiguredPythonPath((cfg.get("pythonPath") || "").trim());
  if (explicit) {
    return explicit;
  }
  for (const p of candidateVenvInterpreterPaths()) {
    return p;
  }
  const py = import_vscode11.workspace.getConfiguration("python");
  const interpreter = expandWorkspaceVars((py.get("defaultInterpreterPath") || "").trim());
  if (interpreter) {
    return interpreter;
  }
  return process.platform === "win32" ? "python" : "python3";
}
function runSemipyCli(args, cwd) {
  return new Promise((resolve3) => {
    const exe = resolvePythonExecutable();
    (0, import_child_process.execFile)(exe, ["-m", "semipy", ...args], { cwd }, (error, stdout, stderr) => {
      let code = 0;
      if (error) {
        const c = error.code;
        code = typeof c === "number" ? c : 1;
      }
      resolve3({
        stdout: String(stdout),
        stderr: String(stderr),
        code
      });
    });
  });
}

// src/features/diagnostics/diagnosticProvider.ts
var fs4 = __toESM(require("fs"));
var path7 = __toESM(require("path"));
var import_vscode12 = require("vscode");
var SemipyDiagnosticManager = class {
  /** semipy `cache_dir` (directory containing `diagnostics.json`). */
  constructor(portalCacheDir) {
    this.portalCacheDir = portalCacheDir;
  }
  collection = import_vscode12.languages.createDiagnosticCollection("semipy");
  dispose() {
    this.collection.dispose();
  }
  refresh() {
    this.collection.clear();
    const cacheDir = this.portalCacheDir();
    if (!cacheDir) {
      return;
    }
    const p = path7.join(cacheDir, "diagnostics.json");
    let data;
    try {
      const raw = fs4.readFileSync(p, "utf8");
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
      const uri = import_vscode12.Uri.file(fp);
      this.collection.set(uri, diags);
    }
  }
  entryToDiagnostic(e) {
    const start = Math.max(1, e.source_line_start) - 1;
    const end = Math.max(1, e.source_line_end) - 1;
    const sev = e.severity === "error" ? import_vscode12.DiagnosticSeverity.Error : e.severity === "warning" ? import_vscode12.DiagnosticSeverity.Warning : import_vscode12.DiagnosticSeverity.Information;
    const d = new import_vscode12.Diagnostic(
      new import_vscode12.Range(new import_vscode12.Position(start, 0), new import_vscode12.Position(end, 2e3)),
      e.message,
      sev
    );
    d.source = "semipy";
    d.code = e.slot_id ? `semi-call-error:${e.slot_id}` : e.code || "semi-call-error";
    d.relatedInformation = [];
    if (e.generated_path && e.generated_line_range?.length === 2) {
      const [a, b] = e.generated_line_range;
      const root = this.portalCacheDir() || "";
      const gp = path7.isAbsolute(e.generated_path) ? e.generated_path : path7.join(root, e.generated_path);
      d.relatedInformation.push({
        location: {
          uri: import_vscode12.Uri.file(gp),
          range: new import_vscode12.Range(
            new import_vscode12.Position(Math.max(1, a) - 1, 0),
            new import_vscode12.Position(Math.max(1, b) - 1, 2e3)
          )
        },
        message: "Generated implementation"
      });
    }
    return d;
  }
};

// src/features/diagnostics/codeActions.ts
var import_vscode13 = require("vscode");
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
      const action = new import_vscode13.CodeAction("Regenerate this spec (semipy CLI)", import_vscode13.CodeActionKind.QuickFix);
      action.command = {
        command: "semipy.regenerateSlotDiagnostic",
        title: "Regenerate",
        arguments: [ws, portal, slotId]
      };
      return [action];
    }
  };
}

// src/features/slotAnnotations/slotEditorAnnotations.ts
var import_vscode14 = require("vscode");

// src/features/slotAnnotations/slotLineResolve.ts
function resolveSlotUiLines(document, slot) {
  const spec = slot.slot_spec;
  if (!spec) {
    return void 0;
  }
  const fsPath = document.uri.fsPath;
  const src = spec.source_span;
  if (!Array.isArray(src) || src.length < 3) {
    return void 0;
  }
  const [fn, start1] = src;
  if (!pathsEqualRobust(fn, fsPath)) {
    return void 0;
  }
  const fullText = document.getText();
  const block = resolveSourceBlockRange(fullText, slot);
  if (!block) {
    return void 0;
  }
  const { startLine1, endLine1 } = block;
  const lines = fullText.split(/\r?\n/);
  let inlayLine1 = startLine1;
  for (let i = startLine1 - 1; i <= endLine1 - 1 && i < lines.length; i++) {
    const raw = lines[i] ?? "";
    if (/#\s*>/.test(raw)) {
      inlayLine1 = i + 1;
      break;
    }
  }
  const start0 = startLine1 - 1;
  let semiformalLine;
  let defLine;
  for (let i = start0; i >= 0 && i >= start0 - 200; i--) {
    const t = document.lineAt(i).text.trim();
    if (t.startsWith("@semiformal")) {
      semiformalLine = i;
    }
    if (t.startsWith("def ") || t.startsWith("async def")) {
      defLine = i;
    }
  }
  const codeLensLine0 = semiformalLine ?? defLine ?? Math.max(0, start1 - 1);
  return { codeLensLine0, inlayLine0: inlayLine1 - 1 };
}

// src/features/slotAnnotations/slotEditorAnnotations.ts
function codeLensLineIndexStale(doc, spec) {
  if (!spec) {
    return void 0;
  }
  const fsPath = doc.uri.fsPath;
  const enc = spec.enclosing_function_span;
  if (Array.isArray(enc) && enc.length >= 2) {
    const [fn2, start12] = enc;
    if (pathsEqualRobust(fn2, fsPath)) {
      return Math.max(0, start12 - 1);
    }
  }
  const src = spec.source_span;
  if (!Array.isArray(src) || src.length < 2) {
    return void 0;
  }
  const [fn, start1] = src;
  if (!pathsEqualRobust(fn, fsPath)) {
    return void 0;
  }
  const firstSpecLine = Math.max(0, start1 - 1);
  let semiformalLine;
  let defLine;
  for (let i = firstSpecLine; i >= 0 && i >= firstSpecLine - 120; i--) {
    const t = doc.lineAt(i).text.trim();
    if (t.startsWith("@semiformal")) {
      semiformalLine = i;
    }
    if (t.startsWith("def ") || t.startsWith("async def")) {
      defLine = i;
    }
  }
  return semiformalLine ?? defLine ?? Math.max(0, firstSpecLine - 1);
}
var SemipyCodeLensProvider = class {
  constructor(getPortal, enabled) {
    this.getPortal = getPortal;
    this.enabled = enabled;
  }
  _onDidChange = new import_vscode14.EventEmitter();
  onDidChangeCodeLenses = this._onDidChange.event;
  refresh() {
    this._onDidChange.fire();
  }
  provideCodeLenses(document) {
    if (!this.enabled()) {
      return [];
    }
    const portal = this.getPortal();
    if (!portal || document.languageId !== "python") {
      return [];
    }
    const fsPath = document.uri.fsPath;
    const out = [];
    for (const slot of Object.values(portal.slots)) {
      const spec = slot.slot_spec;
      const ui = resolveSlotUiLines(document, slot);
      const lineIdx = ui?.codeLensLine0 ?? codeLensLineIndexStale(document, spec);
      if (lineIdx === void 0 || lineIdx >= document.lineCount) {
        continue;
      }
      const range = new import_vscode14.Range(lineIdx, 0, lineIdx, 0);
      const commit = activeCommitFromPortalSlot(slot);
      const idShort = commit?.commit_id?.slice(0, 8) ?? "?";
      const decision = (commit?.decision || "?").toUpperCase();
      const t = commit?.timestamp ? new Date(commit.timestamp * 1e3).toLocaleString() : "";
      const locked = slot.refs?.["__locked__"];
      const headline = locked ? `Semipy locked \xB7 ${idShort} \xB7 ${t}` : `Semipy ${decision} \xB7 ${idShort} \xB7 ${t}`;
      out.push(
        new import_vscode14.CodeLens(range, {
          title: headline,
          command: "semipy.noop"
        }),
        new import_vscode14.CodeLens(range, {
          title: "Switch version",
          command: "semipy.pickSlotVersion",
          arguments: [slot.slot_id]
        }),
        new import_vscode14.CodeLens(range, {
          title: locked ? "Unlock" : "Lock",
          command: locked ? "semipy.unlockSlotVersion" : "semipy.lockSlotVersion",
          arguments: locked ? [slot.slot_id] : [slot.slot_id, commit?.commit_id ?? ""]
        })
      );
    }
    return out;
  }
};
var SemipyInlayHintsProvider = class {
  constructor(getPortal, enabled) {
    this.getPortal = getPortal;
    this.enabled = enabled;
  }
  _onDidChange = new import_vscode14.EventEmitter();
  onDidChangeInlayHints = this._onDidChange.event;
  refresh() {
    this._onDidChange.fire();
  }
  provideInlayHints(document, _range, _token) {
    if (!this.enabled()) {
      return void 0;
    }
    const portal = this.getPortal();
    if (!portal || document.languageId !== "python") {
      return void 0;
    }
    const fsPath = document.uri.fsPath;
    const hints = [];
    for (const slot of Object.values(portal.slots)) {
      const spec = slot.slot_spec;
      const src = spec?.source_span;
      if (!Array.isArray(src) || src.length < 3) {
        continue;
      }
      const [fn] = src;
      if (!pathsEqualRobust(fn, fsPath)) {
        continue;
      }
      const ui = resolveSlotUiLines(document, slot);
      const lineNo = ui?.inlayLine0 ?? Math.max(0, src[1] - 1);
      if (lineNo >= document.lineCount) {
        continue;
      }
      const line = document.lineAt(lineNo);
      const commit = activeCommitFromPortalSlot(slot);
      const decision = (commit?.decision || "?").toUpperCase();
      const idShort = commit?.commit_id?.slice(0, 8) ?? "?";
      const label = ` semipy \xB7 ${decision} \xB7 ${idShort} `;
      hints.push(
        new import_vscode14.InlayHint(
          new import_vscode14.Position(lineNo, line.text.length),
          label,
          import_vscode14.InlayHintKind.Type
        )
      );
    }
    return hints.length ? hints : void 0;
  }
};

// src/features/specCommentSyntax/specCommentSyntaxDecorations.ts
var import_vscode15 = require("vscode");
function createSpecCommentSyntaxTypes() {
  return {
    specMarker: import_vscode15.window.createTextEditorDecorationType({
      fontWeight: "600",
      light: { color: "#008f84" },
      dark: { color: "#4ec9b0" }
    }),
    specBody: import_vscode15.window.createTextEditorDecorationType({
      light: { color: "#007a8a" },
      dark: { color: "#9cdcfe" }
    }),
    reasoningMarker: import_vscode15.window.createTextEditorDecorationType({
      fontWeight: "600",
      light: { color: "#5a8a3d" },
      dark: { color: "#6a9955" }
    }),
    reasoningBody: import_vscode15.window.createTextEditorDecorationType({
      light: { color: "#3d6b2e" },
      dark: { color: "#b5cea8" }
    })
  };
}
function rangesOnLine(line, lineIdx) {
  const spec = [];
  const reasoning = [];
  let pos = 0;
  while (pos < line.length) {
    const gt = line.indexOf("#", pos);
    if (gt < 0) {
      break;
    }
    const slice = line.slice(gt);
    const mGt = slice.match(/^#\s*>/);
    const mLt = slice.match(/^#\s*</);
    if (mGt) {
      const markerStart = gt;
      const markerEnd = gt + mGt[0].length;
      const bodyEnd = line.length;
      spec.push({
        marker: new import_vscode15.Range(new import_vscode15.Position(lineIdx, markerStart), new import_vscode15.Position(lineIdx, markerEnd)),
        body: new import_vscode15.Range(new import_vscode15.Position(lineIdx, markerEnd), new import_vscode15.Position(lineIdx, bodyEnd))
      });
      pos = markerEnd;
      continue;
    }
    if (mLt) {
      const markerStart = gt;
      const markerEnd = gt + mLt[0].length;
      const bodyEnd = line.length;
      reasoning.push({
        marker: new import_vscode15.Range(new import_vscode15.Position(lineIdx, markerStart), new import_vscode15.Position(lineIdx, markerEnd)),
        body: new import_vscode15.Range(new import_vscode15.Position(lineIdx, markerEnd), new import_vscode15.Position(lineIdx, bodyEnd))
      });
      pos = markerEnd;
      continue;
    }
    pos = gt + 1;
  }
  return { spec, reasoning };
}
function refreshSpecCommentSyntaxDecorations(editor, types) {
  if (editor.document.languageId !== "python") {
    editor.setDecorations(types.specMarker, []);
    editor.setDecorations(types.specBody, []);
    editor.setDecorations(types.reasoningMarker, []);
    editor.setDecorations(types.reasoningBody, []);
    return;
  }
  const specM = [];
  const specB = [];
  const reasM = [];
  const reasB = [];
  const n = editor.document.lineCount;
  for (let lineIdx = 0; lineIdx < n; lineIdx++) {
    const line = editor.document.lineAt(lineIdx).text;
    const { spec, reasoning } = rangesOnLine(line, lineIdx);
    for (const s of spec) {
      specM.push(s.marker);
      specB.push(s.body);
    }
    for (const r of reasoning) {
      reasM.push(r.marker);
      reasB.push(r.body);
    }
  }
  editor.setDecorations(types.specMarker, specM);
  editor.setDecorations(types.specBody, specB);
  editor.setDecorations(types.reasoningMarker, reasM);
  editor.setDecorations(types.reasoningBody, reasB);
}
function disposeSpecCommentSyntaxTypes(types) {
  types.specMarker.dispose();
  types.specBody.dispose();
  types.reasoningMarker.dispose();
  types.reasoningBody.dispose();
}

// src/extension.ts
function semipyCliFailureMessage(stderr, stdout, fallback) {
  let detail = (stderr || stdout || fallback).trim().slice(0, 500);
  if (detail.includes("No module named 'semipy'") || detail.includes("No module named semipy")) {
    detail += " Use Python: Select Interpreter for an environment that includes semipy, or set semipy.pythonPath.";
  }
  return detail;
}
function sessionSourceOpts() {
  let raw = import_vscode16.workspace.getConfiguration("semipy").get("sessionSource")?.trim();
  if (raw?.includes("${workspaceFolder}")) {
    const folder = import_vscode16.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (folder) {
      raw = raw.replace(/\$\{workspaceFolder\}/g, folder);
    }
  }
  return { sessionSourceFromSettings: raw || void 0 };
}
function refreshPortalForUri(fsPath, state) {
  const found = findPortalJsonPathForEditor(fsPath, sessionSourceOpts());
  if (!found) {
    state.portal = void 0;
    state.portalPath = void 0;
    state.portalCacheDir = void 0;
    state.workspaceRoot = void 0;
    return;
  }
  const portal = loadPortalJson(found);
  if (!portal) {
    state.portal = void 0;
    state.portalPath = void 0;
    state.portalCacheDir = void 0;
    state.workspaceRoot = void 0;
    return;
  }
  state.portalPath = found;
  state.portal = portal;
  state.portalCacheDir = path8.dirname(found);
  const wf = import_vscode16.workspace.getWorkspaceFolder(import_vscode16.Uri.file(found));
  state.workspaceRoot = wf?.uri.fsPath ?? path8.dirname(state.portalCacheDir);
}
function activate(context) {
  const portalState = {
    portal: void 0,
    portalPath: void 0,
    portalCacheDir: void 0,
    workspaceRoot: void 0
  };
  const cfg = () => import_vscode16.workspace.getConfiguration("semipy");
  const opacityTypes = createOpacityDecorationTypes();
  const phraseTypes = createPhraseDecorationTypes();
  const specSyntaxTypes = createSpecCommentSyntaxTypes();
  const debounceMs = () => cfg().get("debounceMs") ?? 200;
  const signFlip = new SignFlipCoordinator(
    () => cfg().get("signFlipOnSkeletonEdit") ?? true,
    () => cfg().get("signFlipSkipApiEdits") ?? true
  );
  const codeLensProvider = new SemipyCodeLensProvider(
    () => portalState.portal,
    () => cfg().get("enableCodeLens") ?? true
  );
  const inlayProvider = new SemipyInlayHintsProvider(
    () => portalState.portal,
    () => cfg().get("enableInlayHints") ?? true
  );
  const linked = new LinkedHighlightCoordinator(
    () => cfg().get("linkedHighlightFadeMs") ?? 1500
  );
  const diag = new SemipyDiagnosticManager(() => portalState.portalCacheDir);
  const tree = new SlotHistoryProvider(() => portalState.portal);
  const treeView = import_vscode16.window.createTreeView("semipy.slotHistory", {
    treeDataProvider: tree,
    showCollapseAll: true
  });
  const status = import_vscode16.window.createStatusBarItem(import_vscode16.StatusBarAlignment.Left, 100);
  status.command = "semipy.refreshHistory";
  function refreshAllDecorations(editor) {
    if (!editor) {
      return;
    }
    refreshPortalForUri(editor.document.uri.fsPath, portalState);
    const cacheDir = portalState.portalCacheDir;
    refreshOpacityDecorations(editor, opacityTypes.reasoningDim);
    if (cfg().get("enableSpecLineSyntax") ?? true) {
      refreshSpecCommentSyntaxDecorations(editor, specSyntaxTypes);
    } else {
      editor.setDecorations(specSyntaxTypes.specMarker, []);
      editor.setDecorations(specSyntaxTypes.specBody, []);
      editor.setDecorations(specSyntaxTypes.reasoningMarker, []);
      editor.setDecorations(specSyntaxTypes.reasoningBody, []);
    }
    refreshPhraseDecorations(
      editor,
      portalState.portal,
      cacheDir,
      phraseTypes,
      import_vscode16.workspace.workspaceFolders?.map((w) => w.uri.fsPath) ?? []
    );
    const n = portalState.portal ? Object.keys(portalState.portal.slots).length : 0;
    status.text = `Semipy: ${n} slot(s)`;
    status.show();
    tree.refresh();
    diag.refresh();
    codeLensProvider.refresh();
    inlayProvider.refresh();
    linked.onSelectionOrPortal(editor, portalState.portal, cacheDir);
  }
  const opacitySub = subscribeOpacityWrapper(opacityTypes, debounceMs, refreshAllDecorations);
  context.subscriptions.push(
    getSemipyOutputChannel(),
    treeView,
    status,
    { dispose: () => disposeSpecCommentSyntaxTypes(specSyntaxTypes) },
    opacitySub,
    signFlip.attach(),
    { dispose: () => linked.dispose() },
    diag,
    import_vscode16.languages.registerCodeLensProvider({ language: "python", scheme: "file" }, codeLensProvider),
    import_vscode16.languages.registerInlayHintsProvider({ language: "python", scheme: "file" }, inlayProvider),
    import_vscode16.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("semipy")) {
        codeLensProvider.refresh();
        inlayProvider.refresh();
        refreshAllDecorations(import_vscode16.window.activeTextEditor);
      }
    }),
    import_vscode16.window.onDidChangeTextEditorSelection((e) => {
      refreshPortalForUri(e.textEditor.document.uri.fsPath, portalState);
      linked.onSelectionOrPortal(e.textEditor, portalState.portal, portalState.portalCacheDir);
    }),
    import_vscode16.window.onDidChangeActiveTextEditor((ed) => {
      if (ed) {
        signFlip.seedDocument(ed.document);
      }
      refreshAllDecorations(ed);
    }),
    import_vscode16.languages.registerHoverProvider(
      { language: "python", scheme: "file" },
      createPhraseHoverProvider(
        () => portalState.portal,
        () => portalState.portalCacheDir,
        () => import_vscode16.workspace.workspaceFolders?.map((w) => w.uri.fsPath) ?? []
      )
    ),
    registerCommitTextProvider(),
    import_vscode16.commands.registerCommand("semipy.noop", () => {
    }),
    import_vscode16.commands.registerCommand("semipy.showOutput", () => {
      getSemipyOutputChannel().show(true);
    }),
    import_vscode16.commands.registerCommand("semipy.openSplitView", async () => {
      const ed = import_vscode16.window.activeTextEditor;
      if (!ed) {
        return;
      }
      refreshPortalForUri(ed.document.uri.fsPath, portalState);
      if (!portalState.portal || !portalState.portalCacheDir) {
        void import_vscode16.window.showWarningMessage("Semipy: no portal for this file.");
        return;
      }
      await openDispatchSplitView(portalState.portalCacheDir, portalState.portal.module_name);
    }),
    import_vscode16.commands.registerCommand("semipy.refreshHistory", () => {
      const ed = import_vscode16.window.activeTextEditor;
      refreshAllDecorations(ed);
      tree.refresh();
    }),
    import_vscode16.commands.registerCommand("semipy.pickSlotVersion", async (slotId) => {
      const ed = import_vscode16.window.activeTextEditor;
      if (!ed) {
        return;
      }
      refreshPortalForUri(ed.document.uri.fsPath, portalState);
      const portal = portalState.portal;
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!portal || !root || !portalPath) {
        void import_vscode16.window.showWarningMessage("Semipy: no portal for this file.");
        return;
      }
      const slot = portal.slots[slotId];
      if (!slot) {
        return;
      }
      const commits = Object.values(slot.commits).sort((a, b) => b.timestamp - a.timestamp);
      const picked = await import_vscode16.window.showQuickPick(
        commits.map((c) => ({
          label: c.commit_id.slice(0, 8),
          description: `${c.decision} ${(c.message || "").slice(0, 48)}`,
          detail: new Date(c.timestamp * 1e3).toLocaleString(),
          cid: c.commit_id
        })),
        { placeHolder: "Activate commit (rollback branch head to here)" }
      );
      if (!picked || !("cid" in picked)) {
        return;
      }
      const rel = path8.relative(root, portalPath);
      const r = await runSemipyCli(
        ["rollback", "--portal", rel, "--slot-id", slotId, "--commit-id", picked.cid],
        root
      );
      if (r.code !== 0 && r.code !== null) {
        void import_vscode16.window.showErrorMessage(
          `Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "rollback failed")}`
        );
        return;
      }
      void import_vscode16.window.showInformationMessage("Semipy: rollback complete. Re-run code to rebuild dispatch if needed.");
      refreshAllDecorations(ed);
    }),
    import_vscode16.commands.registerCommand("semipy.lockSlotVersion", async (slotId, commitId) => {
      const ed = import_vscode16.window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!root || !portalPath || !commitId) {
        void import_vscode16.window.showErrorMessage("Semipy: no portal or commit for lock.");
        return;
      }
      const rel = path8.relative(root, portalPath);
      const r = await runSemipyCli(
        ["lock", "--portal", rel, "--slot-id", slotId, "--commit-id", commitId],
        root
      );
      if (r.code !== 0 && r.code !== null) {
        void import_vscode16.window.showErrorMessage(`Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "lock failed")}`);
        return;
      }
      void import_vscode16.window.showInformationMessage((r.stderr || r.stdout || "Lock saved.").trim().slice(0, 400));
      refreshAllDecorations(import_vscode16.window.activeTextEditor);
    }),
    import_vscode16.commands.registerCommand("semipy.unlockSlotVersion", async (slotId) => {
      const ed = import_vscode16.window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!root || !portalPath) {
        void import_vscode16.window.showWarningMessage("Semipy: no portal for this file.");
        return;
      }
      const rel = path8.relative(root, portalPath);
      const r = await runSemipyCli(["unlock", "--portal", rel, "--slot-id", slotId], root);
      if (r.code !== 0 && r.code !== null) {
        void import_vscode16.window.showErrorMessage(`Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "unlock failed")}`);
        return;
      }
      void import_vscode16.window.showInformationMessage((r.stderr || r.stdout || "Unlocked.").trim().slice(0, 400));
      refreshAllDecorations(import_vscode16.window.activeTextEditor);
    }),
    import_vscode16.commands.registerCommand(
      "semipy.viewGeneratedCode",
      async (slotId, commitId) => {
        const ed = import_vscode16.window.activeTextEditor;
        const fsPath = ed?.document.uri.fsPath;
        const portalPath = fsPath && findPortalJsonPathForEditor(fsPath, sessionSourceOpts()) || portalState.portalPath;
        const portal = portalPath ? loadPortalJson(portalPath) : portalState.portal;
        const slot = portal?.slots[slotId];
        const src = slot?.commits[commitId]?.generated_source;
        if (!src) {
          void import_vscode16.window.showWarningMessage(
            "Semipy: commit source not loaded. Refresh history or open the source file that owns this portal."
          );
          return;
        }
        await viewGeneratedCode(slotId, commitId, src);
      }
    ),
    import_vscode16.commands.registerCommand(
      "semipy.regenerateSlotDiagnostic",
      async (ws, portalRel, slotId) => {
        const r = await runSemipyCli(
          ["regenerate", "--portal", portalRel, "--slot-id", slotId],
          ws
        );
        if (r.code !== 0 && r.code !== null) {
          void import_vscode16.window.showErrorMessage(
            `Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "regenerate failed")}`
          );
          return;
        }
        void import_vscode16.window.showInformationMessage(r.stderr || r.stdout || "semipy regenerate finished.");
        diag.refresh();
      }
    ),
    import_vscode16.languages.registerCodeActionsProvider(
      { language: "python", scheme: "file" },
      createRegenerateCodeActionProvider(
        () => portalState.workspaceRoot,
        () => portalState.portalPath && portalState.workspaceRoot ? path8.relative(portalState.workspaceRoot, portalState.portalPath) : void 0
      )
    )
  );
  const ed0 = import_vscode16.window.activeTextEditor;
  if (ed0) {
    signFlip.seedDocument(ed0.document);
  }
  refreshAllDecorations(ed0);
  if (import_vscode16.workspace.workspaceFolders?.length) {
    const wf = import_vscode16.workspace.workspaceFolders[0].uri.fsPath;
    let timer;
    const fire = () => {
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(() => {
        timer = void 0;
        refreshAllDecorations(import_vscode16.window.activeTextEditor);
      }, debounceMs());
    };
    const wPortal = import_vscode16.workspace.createFileSystemWatcher(new import_vscode16.RelativePattern(wf, "**/*.portal.json"));
    const wSemi = import_vscode16.workspace.createFileSystemWatcher(new import_vscode16.RelativePattern(wf, "**/*.semi.py"));
    wPortal.onDidChange(fire);
    wPortal.onDidCreate(fire);
    wPortal.onDidDelete(fire);
    wSemi.onDidChange(fire);
    wSemi.onDidCreate(fire);
    wSemi.onDidDelete(fire);
    context.subscriptions.push(wPortal, wSemi);
  }
}
function subscribeOpacityWrapper(types, debounceMs, onRefresh) {
  let timer;
  const tick = () => {
    onRefresh(import_vscode16.window.activeTextEditor);
  };
  const sub1 = import_vscode16.window.onDidChangeActiveTextEditor(() => {
    tick();
  });
  const sub2 = import_vscode16.workspace.onDidChangeTextDocument((ev) => {
    const ed = import_vscode16.window.activeTextEditor;
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
