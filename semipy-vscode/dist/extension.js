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
var path10 = __toESM(require("path"));
var import_vscode22 = require("vscode");

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
  const env2 = (process.env.SEMIPY_SESSION_SOURCE || "").trim();
  if (env2) {
    return path.resolve(env2);
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
  const env2 = (process.env.SEMIPY_SESSION_SOURCE || "").trim();
  if (env2) {
    pushId(() => sessionIdFromFilename(path.resolve(env2)));
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

// src/features/commentOpacity/dispatchOpacity.ts
var fs2 = __toESM(require("fs"));
var path2 = __toESM(require("path"));
var import_vscode2 = require("vscode");
function createDispatchOpacityType() {
  return import_vscode2.window.createTextEditorDecorationType({ isWholeLine: true, opacity: "0.5" });
}
function isDispatchFile(fsPath) {
  const norm = fsPath.replace(/\\/g, "/");
  return /\/runtime\/[^/]+\.semi\.py$/.test(norm);
}
function loadPortalForDispatch(fsPath) {
  const moduleName = path2.basename(fsPath).replace(/\.semi\.py$/, "");
  const cacheDir = path2.dirname(path2.dirname(fsPath));
  let entries;
  try {
    entries = fs2.readdirSync(cacheDir).filter((f) => f.endsWith(".portal.json"));
  } catch {
    return void 0;
  }
  for (const f of entries) {
    try {
      const p = JSON.parse(fs2.readFileSync(path2.join(cacheDir, f), "utf8"));
      if (p.module_name === moduleName) {
        return p;
      }
    } catch {
    }
  }
  return void 0;
}
function committedLineSet(portal) {
  const set = /* @__PURE__ */ new Set();
  for (const slot of Object.values(portal.slots)) {
    for (const commit of Object.values(slot.commits || {})) {
      const src = commit.generated_source || "";
      for (const raw of src.split(/\r?\n/)) {
        const t = raw.trim();
        if (t) {
          set.add(t);
        }
      }
    }
  }
  return set;
}
function isGeneratedLine(text, committed) {
  const t = text.trim();
  if (!t) {
    return true;
  }
  if (t.startsWith("#")) {
    return true;
  }
  return committed.has(t);
}
function refreshDispatchOpacity(editor, dimType) {
  const fsPath = editor.document.uri.fsPath;
  if (!isDispatchFile(fsPath)) {
    editor.setDecorations(dimType, []);
    return;
  }
  const portal = loadPortalForDispatch(fsPath);
  if (!portal) {
    editor.setDecorations(dimType, []);
    return;
  }
  const committed = committedLineSet(portal);
  const dim = [];
  const n = editor.document.lineCount;
  for (let i = 0; i < n; i++) {
    const text = editor.document.lineAt(i).text;
    if (isGeneratedLine(text, committed)) {
      dim.push(new import_vscode2.Range(i, 0, i, 0));
    }
  }
  editor.setDecorations(dimType, dim);
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
  addToPath: function addToPath(path11, added, removed, oldPosInc) {
    var last = path11.lastComponent;
    if (last && last.added === added && last.removed === removed) {
      return {
        oldPos: path11.oldPos + oldPosInc,
        lastComponent: {
          count: last.count + 1,
          added,
          removed,
          previousComponent: last.previousComponent
        }
      };
    } else {
      return {
        oldPos: path11.oldPos + oldPosInc,
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
  join: function join3(chars) {
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
var import_vscode3 = require("vscode");
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
    const sub = import_vscode3.workspace.onDidChangeTextDocument((e) => this.onChange(e));
    return {
      dispose: () => sub.dispose()
    };
  }
  onChange(e) {
    if (e.document.languageId !== "python") {
      return;
    }
    const uriKey = e.document.uri.toString();
    if (e.reason === import_vscode3.TextDocumentChangeReason.Undo || e.reason === import_vscode3.TextDocumentChangeReason.Redo) {
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
    const edit = new import_vscode3.WorkspaceEdit();
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
    void import_vscode3.workspace.applyEdit(edit).then(
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

// src/features/health/gutterHealth.ts
var path4 = __toESM(require("path"));
var import_vscode4 = require("vscode");

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
  const enc = spec.enclosing_function_span;
  let preferredAnchor;
  if (Array.isArray(enc) && enc.length >= 2) {
    const encPath = enc[0];
    const encStart1 = Number(enc[1]);
    if (typeof encPath === "string" && pathsEqualRobust(encPath, fsPath) && Number.isFinite(encStart1) && encStart1 >= 1 && encStart1 - 1 < document.lineCount) {
      preferredAnchor = encStart1 - 1;
    }
  }
  let semiformalLine;
  let defLine;
  if (preferredAnchor === void 0) {
    for (let i = start0; i >= 0 && i >= start0 - 200; i--) {
      const t = document.lineAt(i).text.trim();
      if (semiformalLine === void 0 && t.startsWith("@semiformal")) {
        semiformalLine = i;
        break;
      }
      if (defLine === void 0 && (t.startsWith("def ") || t.startsWith("async def"))) {
        defLine = i;
      }
    }
  }
  const codeLensLine0 = preferredAnchor ?? semiformalLine ?? defLine ?? Math.max(0, start1 - 1);
  return { codeLensLine0, inlayLine0: inlayLine1 - 1 };
}

// src/features/intelligence/slotInsight.ts
var GUARANTEE_GLOSSARY = {
  non_empty: "output is never empty",
  non_identity: "output actually transforms the input (never echoes it back)",
  type_match: "output is always the declared type",
  category_preserving: "output stays in the same category as the input",
  idempotent: "applying it twice gives the same result as once",
  whitespace_invariance: "leading/trailing whitespace does not change the result",
  case_invariance: "input letter-casing does not change the result"
};
function assertionKey(c) {
  if (c.kind === "invariant") {
    return `inv:${c.invariant}:${c.expected_type || ""}`;
  }
  if (c.kind === "metamorphic") {
    return `mr:${c.relation}`;
  }
  return `ex:${c.case_id}`;
}
function guaranteeLabel(c) {
  if (c.kind === "invariant") {
    return `${c.invariant}${c.expected_type ? `=${c.expected_type}` : ""}`;
  }
  if (c.kind === "metamorphic") {
    return c.relation || "relation";
  }
  return "example";
}
function guaranteeMeaning(c) {
  if (c.kind === "invariant") {
    return GUARANTEE_GLOSSARY[c.invariant || ""] || "a structural property of the output";
  }
  if (c.kind === "metamorphic") {
    return GUARANTEE_GLOSSARY[c.relation || ""] || "a relation between an input and a transformed input";
  }
  return "a pinned input -> output example";
}
var SEEDED_REASONS = /* @__PURE__ */ new Set(["initial behavior", "adapted for new input pattern"]);
function primarySampleRepr(c) {
  const sample = c.input_sample || {};
  for (const [k, v] of Object.entries(sample)) {
    if (k === "self" || k.startsWith("_")) {
      continue;
    }
    const s = typeof v === "string" ? v : JSON.stringify(v);
    const t = (s ?? "").replace(/\s+/g, " ").trim();
    return t.length <= 48 ? t : t.slice(0, 47) + "\u2026";
  }
  return "";
}
function groupGuarantees(cases) {
  const byKey = /* @__PURE__ */ new Map();
  for (const c of cases) {
    const status = c.status || "active";
    if (status === "superseded") {
      continue;
    }
    const key = assertionKey(c);
    let g = byKey.get(key);
    if (!g) {
      g = { rep: c, fps: /* @__PURE__ */ new Set(), quarantined: 0, caseIds: [] };
      byKey.set(key, g);
    }
    if (status === "quarantined") {
      g.quarantined += 1;
    } else {
      g.fps.add(c.input_fingerprint || "");
      if (c.case_id) {
        g.caseIds.push(c.case_id);
      }
      if (SEEDED_REASONS.has((g.rep.reason || "").trim()) && !SEEDED_REASONS.has((c.reason || "").trim())) {
        g.rep = c;
      }
    }
  }
  const out = [];
  for (const [key, g] of byKey) {
    const rep = g.rep;
    const reason = (rep.reason || "").trim();
    out.push({
      key,
      kind: rep.kind,
      label: guaranteeLabel(rep),
      meaning: guaranteeMeaning(rep),
      patterns: g.fps.size,
      reason: SEEDED_REASONS.has(reason) ? "" : reason,
      quarantined: g.quarantined,
      caseIds: g.caseIds,
      sampleRepr: primarySampleRepr(rep)
    });
  }
  const order = { invariant: 0, metamorphic: 1, example: 2 };
  return out.sort((a, b) => (order[a.kind] ?? 9) - (order[b.kind] ?? 9));
}
var LOCK_REF2 = "__locked__";
function decisionGlyph(decision) {
  switch ((decision || "").toUpperCase()) {
    case "GENERATE":
      return "\u25C6";
    case "ADAPT":
      return "\u25D0";
    case "REUSE":
      return "\u21BB";
    case "INSTANTIATE":
      return "\u29C9";
    case "FORK":
      return "\u2387";
    case "MERGE":
      return "\u2A07";
    default:
      return "\u25C7";
  }
}
function activeCases(contract, status) {
  const cases = contract?.cases;
  if (!cases) {
    return [];
  }
  return Object.values(cases).filter((c) => (c.status || "active") === status);
}
function contractInsight(slot) {
  const contract = slot.contract;
  const all = contract?.cases ? Object.values(contract.cases) : [];
  const guarantees = groupGuarantees(all);
  return {
    active: activeCases(contract, "active").length,
    superseded: activeCases(contract, "superseded").length,
    quarantined: activeCases(contract, "quarantined").length,
    distinct: guarantees.filter((g) => g.patterns > 0).length,
    guarantees
  };
}
function changeInsight(commit) {
  const cr = commit?.change_record;
  if (!cr || !cr.reason && !(cr.effect_diff && cr.effect_diff.length)) {
    return void 0;
  }
  const diffs = (cr.effect_diff || []).map((d) => ({
    oldRepr: String(d.old_repr ?? ""),
    newRepr: String(d.new_repr ?? ""),
    intended: !!d.intended,
    inputRepr: String(d.input_repr ?? "")
  }));
  const unintended = typeof cr.unintended_count === "number" ? cr.unintended_count : diffs.filter((d) => !d.intended).length;
  return {
    reason: (cr.reason || "").trim(),
    decision: cr.decision || commit?.decision || "",
    intended: diffs.filter((d) => d.intended).length,
    unintended,
    compared: typeof cr.n_compared === "number" ? cr.n_compared : diffs.length,
    hasRegression: unintended > 0,
    diffs
  };
}
function distinctTargets(effects) {
  return [...new Set((effects || []).map((e) => e.target).filter(Boolean))];
}
function distinctOps(effects) {
  return [...new Set((effects || []).map((e) => e.op).filter(Boolean))];
}
function effectInsight(slot) {
  const events = slot.ledger?.events || [];
  const applied = events.filter((e) => (e.status || "applied") === "applied");
  const reverted = events.filter((e) => e.status === "reverted");
  const pending = events.filter(
    (e) => e.status === "approval_pending" || e.status === "shadow"
  );
  const latest = events.length ? events[events.length - 1] : void 0;
  const latestEffects = latest?.applied_effects || [];
  const mutating = latestEffects.filter((e) => e.op !== "read" && e.op !== "call");
  const reversible = mutating.length > 0 && mutating.every((e) => !!e.compensation);
  return {
    isEffectful: events.length > 0,
    applied: applied.length,
    reverted: reverted.length,
    pending: pending.length,
    targets: distinctTargets(latestEffects),
    reversible,
    latestStatus: latest?.status || "",
    latestEventId: latest?.event_id || "",
    latestOps: distinctOps(latestEffects)
  };
}
function computeSlotInsight(slot) {
  if (!slot.commits || Object.keys(slot.commits).length === 0) {
    return void 0;
  }
  const commit = activeCommitFromPortalSlot(slot);
  const contract = contractInsight(slot);
  const change = changeInsight(commit);
  const effect = effectInsight(slot);
  const locked = !!slot.refs?.[LOCK_REF2];
  let health = "ok";
  if (change?.hasRegression) {
    health = "danger";
  } else if (contract.quarantined > 0 || effect.pending > 0) {
    health = "warn";
  } else if (effect.isEffectful) {
    health = "effect";
  }
  return {
    decision: (commit?.decision || "?").toUpperCase(),
    glyph: decisionGlyph(commit?.decision || ""),
    commitShort: commit?.commit_id?.slice(0, 8) ?? "?",
    timestamp: commit?.timestamp ?? 0,
    locked,
    health,
    contract,
    change,
    effect
  };
}
function insightChips(insight) {
  const chips = [`${insight.glyph} ${insight.decision}`];
  if (insight.locked) {
    chips.push("locked");
  }
  const c = insight.contract;
  if (c.distinct > 0) {
    chips.push(`\u2713${c.distinct} hold`);
  }
  if (c.quarantined > 0) {
    chips.push(`\u26A0${c.quarantined} quarantined`);
  }
  if (insight.change?.hasRegression) {
    chips.push(`\u26A0${insight.change.unintended} regression${insight.change.unintended === 1 ? "" : "s"}`);
  }
  const e = insight.effect;
  if (e.isEffectful) {
    const tgt = e.targets[0] || "effect";
    const more = e.targets.length > 1 ? ` +${e.targets.length - 1}` : "";
    let state = e.latestStatus || "applied";
    if (state === "approval_pending") {
      state = "approval pending";
    }
    chips.push(`\u26A1 ${tgt}${more} ${state}`);
  }
  return chips;
}

// src/features/health/gutterHealth.ts
function icon(extensionPath, name) {
  return import_vscode4.Uri.file(path4.join(extensionPath, "images", "gutter", `slot-${name}.svg`));
}
function createGutterHealthTypes(extensionPath) {
  const make = (name, ruler) => import_vscode4.window.createTextEditorDecorationType({
    gutterIconPath: icon(extensionPath, name),
    gutterIconSize: "contain",
    ...ruler ? { overviewRulerColor: ruler, overviewRulerLane: import_vscode4.OverviewRulerLane.Right } : {}
  });
  return {
    ok: make("ok"),
    effect: make("effect", "rgba(215,166,95,0.55)"),
    warn: make("warn", "rgba(215,166,95,0.85)"),
    danger: make("danger", "rgba(209,105,105,0.9)")
  };
}
function refreshGutterHealth(editor, portal, types) {
  const clear = () => {
    editor.setDecorations(types.ok, []);
    editor.setDecorations(types.effect, []);
    editor.setDecorations(types.warn, []);
    editor.setDecorations(types.danger, []);
  };
  if (!portal || editor.document.languageId !== "python") {
    clear();
    return;
  }
  const fsPath = editor.document.uri.fsPath;
  const buckets = { ok: [], effect: [], warn: [], danger: [] };
  for (const slot of Object.values(portal.slots)) {
    if (!slot.commits || Object.keys(slot.commits).length === 0) {
      continue;
    }
    const src = slot.slot_spec?.source_span;
    if (!Array.isArray(src) || src.length < 3 || !pathsEqualRobust(src[0], fsPath)) {
      continue;
    }
    const ui = resolveSlotUiLines(editor.document, slot);
    const line0 = ui?.codeLensLine0;
    if (line0 === void 0 || line0 >= editor.document.lineCount) {
      continue;
    }
    const insight = computeSlotInsight(slot);
    if (!insight) {
      continue;
    }
    buckets[insight.health].push(new import_vscode4.Range(line0, 0, line0, 0));
  }
  editor.setDecorations(types.ok, buckets.ok);
  editor.setDecorations(types.effect, buckets.effect);
  editor.setDecorations(types.warn, buckets.warn);
  editor.setDecorations(types.danger, buckets.danger);
}
function disposeGutterHealthTypes(types) {
  types.ok.dispose();
  types.effect.dispose();
  types.warn.dispose();
  types.danger.dispose();
}

// src/features/versionTree/versionModel.ts
var LOCK_REF3 = "__locked__";
function compareCommits(a, b) {
  if (a.timestamp !== b.timestamp) {
    return a.timestamp - b.timestamp;
  }
  return a.commit_id < b.commit_id ? -1 : a.commit_id > b.commit_id ? 1 : 0;
}
function orderedVersions(slot) {
  const commits = Object.values(slot.commits || {});
  if (commits.length === 0) {
    return [];
  }
  const sorted = [...commits].sort(compareCommits);
  const activeId = activeCommitFromPortalSlot(slot)?.commit_id;
  const lockedId = slot.refs?.[LOCK_REF3];
  return sorted.map((commit, i) => ({
    commit,
    version: i + 1,
    isActive: commit.commit_id === activeId,
    isLocked: !!lockedId && commit.commit_id === lockedId
  }));
}
function activeVersion(slot) {
  const versions = orderedVersions(slot);
  if (versions.length === 0) {
    return void 0;
  }
  return versions.find((v) => v.isActive) ?? versions[versions.length - 1];
}
function versionLensLabel(slot) {
  const versions = orderedVersions(slot);
  if (versions.length === 0) {
    return void 0;
  }
  const active = activeVersion(slot);
  if (!active) {
    return void 0;
  }
  const pinned = versions.some((v) => v.isLocked) ? " \xB7 pinned" : "";
  return `v${active.version}/${versions.length}${pinned}`;
}

// src/features/intelligence/slotInsightHoverProvider.ts
var import_vscode5 = require("vscode");

// src/features/intelligence/explanationCard.ts
function relativeTime(tsSeconds) {
  if (!tsSeconds) {
    return "";
  }
  const deltaMs = Date.now() - tsSeconds * 1e3;
  const s = Math.round(deltaMs / 1e3);
  if (s < 0) {
    return "just now";
  }
  if (s < 60) {
    return `${s}s ago`;
  }
  const m = Math.round(s / 60);
  if (m < 60) {
    return `${m}m ago`;
  }
  const h = Math.round(m / 60);
  if (h < 24) {
    return `${h}h ago`;
  }
  const d = Math.round(h / 24);
  if (d < 30) {
    return `${d}d ago`;
  }
  return new Date(tsSeconds * 1e3).toLocaleDateString();
}
function truncate(s, n) {
  const t = (s || "").replace(/\s+/g, " ").trim();
  return t.length <= n ? t : t.slice(0, n - 1) + "\u2026";
}
function cmdLink(label, command, args) {
  const q = encodeURIComponent(JSON.stringify(args));
  return `[${label}](command:${command}?${q})`;
}
function constraintLine(slot) {
  const s = slot.slot_spec;
  if (!s) {
    return "";
  }
  const bits = [];
  if (s.expected_type) {
    bits.push(`returns \`${truncate(s.expected_type, 40)}\``);
  }
  const outs = s.output_names;
  if (Array.isArray(outs) && outs.length) {
    bits.push(`as \`${outs.join(", ")}\``);
  }
  const ctx = s.control_context;
  if (ctx && ctx !== "none") {
    bits.push(`in a \`${ctx}\``);
  }
  return bits.join(" ");
}
function buildHoverMarkdown(slot, insight) {
  const lines = [];
  const lockBadge = insight.locked ? " \xB7 $(lock) locked" : "";
  const t = relativeTime(insight.timestamp);
  lines.push(
    `**${insight.glyph} ${insight.decision}** \xB7 \`${insight.commitShort}\`${t ? ` \xB7 ${t}` : ""}${lockBadge}`
  );
  lines.push("");
  if (insight.change?.reason) {
    lines.push(`$(info) **Why** \u2014 ${truncate(insight.change.reason, 180)}`);
    lines.push("");
  }
  if (insight.change && (insight.change.compared > 0 || insight.change.diffs.length > 0)) {
    const ch = insight.change;
    const reg = ch.hasRegression ? `$(warning) **${ch.unintended} unintended**` : "0 unintended";
    lines.push(`$(diff) **Effect** \u2014 +${ch.intended} changed \xB7 ${reg}  *(over ${ch.compared} input pattern${ch.compared === 1 ? "" : "s"})*`);
    for (const d of ch.diffs.slice(0, 2)) {
      const mark = d.intended ? "" : " $(warning)";
      lines.push(`> \`${truncate(d.oldRepr, 36)}\` \u2192 \`${truncate(d.newRepr, 36)}\`${mark}`);
    }
    lines.push("");
  }
  const guarantees = insight.contract.guarantees.filter((g) => g.patterns > 0);
  if (guarantees.length) {
    lines.push(`$(law) **Guarantees**`);
    for (const g of guarantees.slice(0, 8)) {
      const span = g.patterns > 1 ? ` *(across ${g.patterns} input patterns)*` : "";
      const detail = g.reason ? ` \u2014 ${truncate(g.reason, 70)}` : ` \u2014 ${g.meaning}`;
      lines.push(`- \`${g.label}\`${detail}${span}`);
    }
    const quarGroups = insight.contract.guarantees.filter((g) => g.patterns === 0 && g.quarantined > 0);
    const sup = insight.contract.superseded;
    if (quarGroups.length || sup) {
      const bits = [];
      if (quarGroups.length)
        bits.push(`${quarGroups.length} quarantined`);
      if (sup)
        bits.push(`${sup} superseded`);
      lines.push(`- *${bits.join(" \xB7 ")}*`);
    }
    lines.push("");
  }
  const constraint = constraintLine(slot);
  if (constraint) {
    lines.push(`$(symbol-type) **Spec** \u2014 ${constraint}`);
    lines.push("");
  }
  if (insight.effect.isEffectful) {
    const e = insight.effect;
    const ops = e.latestOps.length ? ` (${e.latestOps.join(", ")})` : "";
    const rev = e.reversible ? "reversible" : "$(warning) irreversible";
    const counts = `applied ${e.applied}\xD7${e.reverted ? ` \xB7 reverted ${e.reverted}\xD7` : ""}`;
    lines.push(`$(zap) **Touches** \u2014 ${e.targets.join(", ") || "\u2014"}${ops}`);
    lines.push(`> ${rev} \xB7 ${counts}${e.pending ? ` \xB7 $(warning) ${e.pending} pending approval` : ""}`);
    lines.push("");
  }
  const actions = [
    cmdLink("$(search) Inspect", "semipy.inspectSlot", [slot.slot_id]),
    cmdLink("$(code) View code", "semipy.viewActiveCode", [slot.slot_id])
  ];
  const ncommits = Object.keys(slot.commits || {}).length;
  if (ncommits > 1) {
    actions.push(cmdLink("$(history) Switch version", "semipy.pickSlotVersion", [slot.slot_id]));
  }
  if (insight.effect.applied > 0 && insight.effect.latestEventId) {
    actions.push(
      cmdLink("$(discard) Revert effect", "semipy.revertEffect", [slot.slot_id, insight.effect.latestEventId])
    );
  }
  lines.push(actions.join("  \xB7  "));
  return lines.join("\n");
}

// src/features/intelligence/slotInsightHoverProvider.ts
function createSlotInsightHoverProvider(getPortal, enabled) {
  return {
    provideHover(document, position, _token) {
      if (!enabled() || document.languageId !== "python") {
        return void 0;
      }
      const portal = getPortal();
      if (!portal) {
        return void 0;
      }
      const fsPath = document.uri.fsPath;
      const fullText = document.getText();
      const line0 = position.line;
      for (const slot of Object.values(portal.slots)) {
        if (!slot.commits || Object.keys(slot.commits).length === 0) {
          continue;
        }
        const src = slot.slot_spec?.source_span;
        if (!Array.isArray(src) || src.length < 3 || !pathsEqualRobust(src[0], fsPath)) {
          continue;
        }
        const ui = resolveSlotUiLines(document, slot);
        const block = resolveSourceBlockRange(fullText, slot);
        const anchor0 = ui?.codeLensLine0;
        const inBlock = block && line0 >= block.startLine1 - 1 && line0 <= block.endLine1 - 1;
        const onAnchor = anchor0 !== void 0 && line0 === anchor0;
        if (!inBlock && !onAnchor) {
          continue;
        }
        const insight = computeSlotInsight(slot);
        if (!insight) {
          continue;
        }
        const md = new import_vscode5.MarkdownString(buildHoverMarkdown(slot, insight));
        md.isTrusted = true;
        md.supportThemeIcons = true;
        return new import_vscode5.Hover(md);
      }
      return void 0;
    }
  };
}

// src/features/health/regressionDiagnostics.ts
var import_vscode6 = require("vscode");
function trunc(s, n) {
  const t = (s || "").replace(/\s+/g, " ").trim();
  return t.length <= n ? t : t.slice(0, n - 1) + "\u2026";
}
var RegressionDiagnosticManager = class {
  collection;
  constructor() {
    this.collection = import_vscode6.languages.createDiagnosticCollection("semipy-insight");
  }
  clear() {
    this.collection.clear();
  }
  /** Recompute regression diagnostics for the active editor's file. */
  refresh(editor, portal) {
    if (!editor || !portal || editor.document.languageId !== "python") {
      return;
    }
    const fsPath = editor.document.uri.fsPath;
    const diags = [];
    for (const slot of Object.values(portal.slots)) {
      if (!slot.commits || Object.keys(slot.commits).length === 0) {
        continue;
      }
      const src = slot.slot_spec?.source_span;
      if (!Array.isArray(src) || src.length < 3 || !pathsEqualRobust(src[0], fsPath)) {
        continue;
      }
      const insight = computeSlotInsight(slot);
      const ch = insight?.change;
      if (!ch || !ch.hasRegression) {
        continue;
      }
      const ui = resolveSlotUiLines(editor.document, slot);
      const line0 = ui?.inlayLine0 ?? ui?.codeLensLine0;
      if (line0 === void 0 || line0 >= editor.document.lineCount) {
        continue;
      }
      const line = editor.document.lineAt(line0);
      const example = ch.diffs.find((d2) => !d2.intended);
      const detail = example ? ` e.g. \`${trunc(example.oldRepr, 28)}\` \u2192 \`${trunc(example.newRepr, 28)}\`` : "";
      const msg = `semipy: ${insight.decision} introduced ${ch.unintended} unintended change${ch.unintended === 1 ? "" : "s"} (${ch.intended} intended).${detail}`;
      const d = new import_vscode6.Diagnostic(line.range, msg, import_vscode6.DiagnosticSeverity.Warning);
      d.source = "semipy";
      d.code = `semi-regression:${slot.slot_id}`;
      diags.push(d);
    }
    this.collection.set(editor.document.uri, diags);
  }
  dispose() {
    this.collection.dispose();
  }
};

// src/features/steering/reasoningSteering.ts
var import_vscode7 = require("vscode");
var PROVENANCE_KEYS = /* @__PURE__ */ new Set(["goal", "because", "alt", "given"]);
var EFFECT_KEYS = /* @__PURE__ */ new Set(["commits", "verified", "yields"]);
var KEY_HELP = {
  goal: "what this slot is meant to achieve",
  because: "why semipy chose this implementation (decision rationale)",
  alt: "an alternative the model considered but did not take",
  given: "the inputs / assumptions it was generated under",
  commits: "what behaviour this implementation locks in",
  verified: "what was checked to hold (derived, not synthesised)",
  yields: "the shape of the value it returns"
};
function parseSteeringLine(lineText) {
  const m = lineText.match(/^(\s*)#\s*<\s*([a-zA-Z_]+)\s*:/);
  if (!m) {
    return null;
  }
  const key = m[2].toLowerCase();
  let zone;
  if (PROVENANCE_KEYS.has(key)) {
    zone = "provenance";
  } else if (EFFECT_KEYS.has(key)) {
    zone = "effect";
  }
  if (!zone) {
    return null;
  }
  const keyStart = m.index + m[1].length + lineText.slice(m[1].length).indexOf(m[2]);
  return { key, zone, keyStart, keyEnd: keyStart + m[2].length };
}
function isReasoning(lineText) {
  const s = lineText.replace(/^\s+/, "");
  return s.startsWith("#<") || s.startsWith("# <");
}
function createSteeringCodeActionProvider() {
  return {
    provideCodeActions(document, range, _context, _token) {
      const line = document.lineAt(range.start.line);
      if (!isReasoning(line.text)) {
        return [];
      }
      const parsed = parseSteeringLine(line.text);
      const keyLabel = parsed ? ` (${parsed.key})` : "";
      const pin = new import_vscode7.CodeAction(
        `Semipy: Pin as contract${keyLabel} \u2192 #>`,
        import_vscode7.CodeActionKind.RefactorRewrite
      );
      pin.command = {
        command: "semipy.promoteReasoningLine",
        title: "Pin as contract",
        arguments: [document.uri, range.start.line]
      };
      const dismiss = new import_vscode7.CodeAction("Semipy: Dismiss this note", import_vscode7.CodeActionKind.RefactorRewrite);
      dismiss.command = {
        command: "semipy.dismissReasoningLine",
        title: "Dismiss note",
        arguments: [document.uri, range.start.line]
      };
      return [pin, dismiss];
    }
  };
}
function createSteeringHoverProvider() {
  return {
    provideHover(document, position, _token) {
      const line = document.lineAt(position.line);
      if (!isReasoning(line.text)) {
        return void 0;
      }
      const parsed = parseSteeringLine(line.text);
      const md = new import_vscode7.MarkdownString();
      md.isTrusted = true;
      md.supportThemeIcons = true;
      if (parsed) {
        const zoneLabel = parsed.zone === "provenance" ? "provenance" : "effect";
        md.appendMarkdown(
          `$(lightbulb) **\`${parsed.key}\`** \u2014 ${KEY_HELP[parsed.key] || "an inferred note"}  \xB7  *${zoneLabel}*

`
        );
      } else {
        md.appendMarkdown(`$(lightbulb) **Inferred note** \u2014 semipy's reasoning, not part of your contract.

`);
      }
      const q = encodeURIComponent(JSON.stringify([document.uri.toString(), position.line]));
      md.appendMarkdown(
        `[$(pin) Pin as contract (#>)](command:semipy.promoteReasoningLine?${q})  \xB7  [$(close) Dismiss](command:semipy.dismissReasoningLine?${q})`
      );
      return new import_vscode7.Hover(md);
    }
  };
}

// src/features/steering/modesControl.ts
var import_vscode8 = require("vscode");
var MODE_FLAGS = [
  { flag: "verbose", label: "Verbose pipeline stream", detail: "Show the live generation stream and phase strip." },
  { flag: "contract_gate", label: "Contract gate", detail: "Reject generated code that violates a carried behavioral case; regenerate." },
  { flag: "contract_maintainer", label: "Contract maintainer (LLM)", detail: "Let an LLM propose golden-master examples and metamorphic relations." },
  { flag: "sketch_library_learning", label: "Pattern learning", detail: "Learn NL->code sketches so similar specs can INSTANTIATE without an LLM call." },
  { flag: "effects_enabled", label: "Effects subsystem", detail: "Treat slots that declare fx as effectful (reified real-world effects)." },
  { flag: "effect_staging", label: "Effect staging (shadow)", detail: "Run effects against a shadow of the artifact; capture compensations." },
  { flag: "effect_gate", label: "Effect gate", detail: "Enforce reversibility + bounded blast radius before an effect is allowed.", caution: true },
  { flag: "effect_smt", label: "Effect proofs", detail: "Prove bounded blast radius for all inputs via schema superkeys." },
  { flag: "effect_auto_apply", label: "Auto-apply effects", detail: "Commit verified effects to the real artifact (otherwise dry-run).", caution: true },
  { flag: "effect_require_approval_external", label: "Approve external effects", detail: "Require explicit approval before sending to an external (non-shadowable) target." }
];
function buildConfigureSnippet(flags) {
  if (!flags.length) {
    return "configure()";
  }
  const body = flags.map((f) => `    ${f}=True,`).join("\n");
  return `configure(
${body}
)`;
}
function hasConfigureImport(text) {
  return /from\s+semipy\s+import\s+[^\n]*\bconfigure\b/.test(text) || /\bimport\s+semipy\b/.test(text);
}
async function insertConfigure(editor, snippet) {
  const doc = editor.document;
  let lastImport = -1;
  for (let i = 0; i < Math.min(doc.lineCount, 200); i++) {
    const t = doc.lineAt(i).text.trim();
    if (t.startsWith("import ") || t.startsWith("from ")) {
      lastImport = i;
    } else if (t && !t.startsWith("#") && lastImport >= 0) {
      break;
    }
  }
  const needsImport = !hasConfigureImport(doc.getText());
  const importLine = needsImport ? "from semipy import configure\n" : "";
  const at = new import_vscode8.Position(lastImport + 1, 0);
  const block = `${importLine}${snippet}

`;
  await editor.insertSnippet(new import_vscode8.SnippetString(block.replace(/\$/g, "\\$")), at);
}
async function runSteeringModesQuickPick() {
  const items = MODE_FLAGS.map((m) => ({
    label: `${m.caution ? "$(alert) " : ""}${m.label}`,
    description: m.flag,
    detail: m.detail,
    flag: m.flag
  }));
  const picked = await import_vscode8.window.showQuickPick(items, {
    canPickMany: true,
    title: "Semipy \xB7 Steering \u2014 choose the modes to enable",
    placeHolder: "These map to configure(...) flags. Caution-marked modes change real-world behaviour."
  });
  if (!picked || picked.length === 0) {
    return;
  }
  const snippet = buildConfigureSnippet(picked.map((p) => p.flag));
  const action = await import_vscode8.window.showQuickPick(
    [
      { label: "$(insert) Insert configure() at top of file", id: "insert" },
      { label: "$(clippy) Copy to clipboard", id: "copy" }
    ],
    { title: "Apply steering", placeHolder: snippet.replace(/\n\s*/g, " ") }
  );
  if (!action) {
    return;
  }
  if (action.id === "copy") {
    await import_vscode8.env.clipboard.writeText(snippet);
    void import_vscode8.window.showInformationMessage("Semipy: configure(...) snippet copied to clipboard.");
    return;
  }
  const editor = import_vscode8.window.activeTextEditor;
  if (!editor) {
    await import_vscode8.env.clipboard.writeText(snippet);
    void import_vscode8.window.showInformationMessage("Semipy: no active editor \u2014 snippet copied to clipboard instead.");
    return;
  }
  await insertConfigure(editor, snippet);
}

// src/features/phraseHighlight/phraseHoverProvider.ts
var import_vscode9 = require("vscode");
var import_vscode10 = require("vscode");

// src/data/sketchLoader.ts
var fs3 = __toESM(require("fs"));
var path5 = __toESM(require("path"));
function sketchLibraryPath(cacheDir) {
  return path5.join(cacheDir, "sketch_library.json");
}
function loadSketchLibraryFile(p) {
  try {
    const raw = fs3.readFileSync(p, "utf8");
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
      names = fs3.readdirSync(dir);
    } catch {
      return;
    }
    for (const name of names) {
      if (!name.startsWith(".semiformal")) {
        continue;
      }
      const sub = path5.join(dir, name);
      let isDir = false;
      try {
        isDir = fs3.statSync(sub).isDirectory();
      } catch {
        continue;
      }
      if (!isDir) {
        continue;
      }
      absorb(loadSketchLibraryFile(path5.join(sub, "sketch_library.json")));
    }
  };
  for (const root of workspaceRoots ?? []) {
    scanDir(root);
    let entries;
    try {
      entries = fs3.readdirSync(root, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const ent of entries) {
      if (!ent.isDirectory() || ent.name.startsWith(".")) {
        continue;
      }
      scanDir(path5.join(root, ent.name));
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
              const md = new import_vscode10.MarkdownString();
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
              return new import_vscode9.Hover(md);
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
var import_vscode12 = require("vscode");

// src/logging/semipyOutputChannel.ts
var import_vscode11 = require("vscode");
var channel;
function getSemipyOutputChannel() {
  if (!channel) {
    channel = import_vscode11.window.createOutputChannel("Semipy");
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
    out[role] = import_vscode12.window.createTextEditorDecorationType({
      rangeBehavior: import_vscode12.DecorationRangeBehavior.ClosedClosed,
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
  const trace = import_vscode12.workspace.getConfiguration("semipy").get("tracePhraseDecorations") ?? false;
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
        const r = new import_vscode12.Range(
          new import_vscode12.Position(lineIdx, startCol),
          new import_vscode12.Position(lineIdx, Math.min(endCol, lineObj.text.length))
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
var path6 = __toESM(require("path"));
var import_vscode13 = require("vscode");
var LinkedHighlightCoordinator = class {
  constructor(fadeMs) {
    this.fadeMs = fadeMs;
    this.highlight = import_vscode13.window.createTextEditorDecorationType({
      backgroundColor: new import_vscode13.ThemeColor("editor.wordHighlightBackground"),
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
    for (const ed of import_vscode13.window.visibleTextEditors) {
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
    const dispatchPath = path6.join(portalCacheDir, "runtime", `${portal.module_name}.semi.py`);
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
    const targetUri = import_vscode13.Uri.file(dr.uriPath);
    const dispEd = import_vscode13.window.visibleTextEditors.find(
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
        const srcUri = import_vscode13.Uri.file(srcFile);
        const srcEd = import_vscode13.window.visibleTextEditors.find(
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
      for (const ed of import_vscode13.window.visibleTextEditors) {
        ed.setDecorations(this.highlight, []);
      }
    }, ms);
  }
};

// src/features/splitEditor/splitEditorCommand.ts
var path7 = __toESM(require("path"));
var import_vscode14 = require("vscode");
async function openDispatchSplitView(portalCacheDir, moduleName) {
  const abs = path7.join(portalCacheDir, "runtime", `${moduleName}.semi.py`);
  const uri = import_vscode14.Uri.file(abs);
  try {
    const doc = await import_vscode14.workspace.openTextDocument(uri);
    await import_vscode14.window.showTextDocument(doc, {
      viewColumn: import_vscode14.ViewColumn.Beside,
      preserveFocus: false
    });
  } catch {
    void import_vscode14.window.showErrorMessage(`Semipy: could not open dispatch file: ${abs}`);
  }
}

// src/features/versionTree/slotHistoryProvider.ts
var import_vscode16 = require("vscode");

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
var import_vscode15 = require("vscode");
function decisionIcon(decision) {
  const d = (decision || "").toUpperCase();
  if (d === "GENERATE") {
    return new import_vscode15.ThemeIcon("git-commit");
  }
  if (d === "ADAPT") {
    return new import_vscode15.ThemeIcon("git-merge");
  }
  if (d === "REUSE" || d === "reuse") {
    return new import_vscode15.ThemeIcon("link");
  }
  if (d === "INSTANTIATE" || d === "instantiate") {
    return new import_vscode15.ThemeIcon("puzzle");
  }
  return new import_vscode15.ThemeIcon("git-commit");
}
function healthIcon(health) {
  switch (health) {
    case "danger":
      return new import_vscode15.ThemeIcon("error", new import_vscode15.ThemeColor("errorForeground"));
    case "warn":
      return new import_vscode15.ThemeIcon("warning", new import_vscode15.ThemeColor("charts.yellow"));
    case "effect":
      return new import_vscode15.ThemeIcon("circle-outline", new import_vscode15.ThemeColor("charts.yellow"));
    default:
      return new import_vscode15.ThemeIcon("circle-filled", new import_vscode15.ThemeColor("charts.green"));
  }
}
function guaranteeIcon(g) {
  if (g.patterns === 0 && g.quarantined > 0) {
    return new import_vscode15.ThemeIcon("warning", new import_vscode15.ThemeColor("charts.yellow"));
  }
  if (g.kind === "invariant") {
    return new import_vscode15.ThemeIcon("shield", new import_vscode15.ThemeColor("charts.green"));
  }
  if (g.kind === "metamorphic") {
    return new import_vscode15.ThemeIcon("symbol-operator", new import_vscode15.ThemeColor("charts.blue"));
  }
  return new import_vscode15.ThemeIcon("bookmark", new import_vscode15.ThemeColor("charts.green"));
}
function eventIcon(e) {
  const status = e.status || "applied";
  if (status === "reverted") {
    return new import_vscode15.ThemeIcon("discard");
  }
  if (status === "approval_pending" || status === "shadow") {
    return new import_vscode15.ThemeIcon("clock", new import_vscode15.ThemeColor("charts.yellow"));
  }
  return new import_vscode15.ThemeIcon("zap", new import_vscode15.ThemeColor("charts.yellow"));
}
function eventLabel(e) {
  const ops = [...new Set((e.applied_effects || []).map((x) => x.op))];
  const targets = [...new Set((e.applied_effects || []).map((x) => x.target).filter(Boolean))];
  const head = targets.length ? targets.join(", ") : (e.event_id || "").slice(0, 8);
  return `${ops.join("/")} ${head}`.trim();
}
function truncateSpecPreview(spec, n = 60) {
  const t = spec.replace(/\s+/g, " ").trim();
  if (t.length <= n) {
    return t;
  }
  return t.slice(0, n - 1) + "\u2026";
}

// src/features/versionTree/slotHistoryProvider.ts
function elementId(e) {
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
var SlotHistoryProvider = class {
  constructor(getPortal) {
    this.getPortal = getPortal;
  }
  _onDidChange = new import_vscode16.EventEmitter();
  onDidChangeTreeData = this._onDidChange.event;
  refresh() {
    this._onDidChange.fire(void 0);
  }
  /** A tree element for a slot id (for treeView.reveal from the Inspect action). */
  slotElement(slotId) {
    const portal = this.getPortal();
    const slot = portal?.slots?.[slotId];
    if (!portal || !slot) {
      return void 0;
    }
    return { kind: "slot", portal, slot };
  }
  getTreeItem(element) {
    const ti = this.buildTreeItem(element);
    ti.id = elementId(element);
    return ti;
  }
  buildTreeItem(element) {
    if (element.kind === "portal") {
      const ti2 = new import_vscode16.TreeItem(
        element.portal.source_file || element.portal.module_name || "portal",
        import_vscode16.TreeItemCollapsibleState.Expanded
      );
      ti2.description = element.portal.module_name;
      ti2.iconPath = new import_vscode16.ThemeIcon("symbol-namespace");
      return ti2;
    }
    if (element.kind === "slot") {
      const spec = element.slot.slot_spec?.spec_text || "";
      const insight = computeSlotInsight(element.slot);
      const ti2 = new import_vscode16.TreeItem(
        truncateSpecPreview(spec) || element.slot.slot_id.slice(0, 8),
        import_vscode16.TreeItemCollapsibleState.Expanded
      );
      ti2.description = insight ? `${insight.glyph} ${insight.decision} \xB7 ${insight.commitShort}` : element.slot.slot_id.slice(0, 8);
      ti2.iconPath = insight ? healthIcon(insight.health) : new import_vscode16.ThemeIcon("circle-outline");
      ti2.contextValue = "semipy.slot";
      ti2.command = {
        command: "semipy.viewActiveCode",
        title: "View active implementation",
        arguments: [element.slot.slot_id]
      };
      return ti2;
    }
    if (element.kind === "contractGroup") {
      const c = computeSlotInsight(element.slot)?.contract;
      const ti2 = new import_vscode16.TreeItem("Guarantees", import_vscode16.TreeItemCollapsibleState.Collapsed);
      ti2.iconPath = new import_vscode16.ThemeIcon("law");
      if (c) {
        const patterns = new Set(
          Object.values(element.slot.contract?.cases || {}).map((x) => x.input_fingerprint || "")
        ).size;
        const bits = [`${c.distinct} distinct`];
        if (patterns > 1)
          bits.push(`${patterns} patterns`);
        if (c.quarantined)
          bits.push(`${c.quarantined} quarantined`);
        ti2.description = bits.join(" \xB7 ");
      }
      return ti2;
    }
    if (element.kind === "guarantee") {
      const g = element.guarantee;
      const quarantined = g.patterns === 0 && g.quarantined > 0;
      const ti2 = new import_vscode16.TreeItem(g.label, import_vscode16.TreeItemCollapsibleState.None);
      ti2.iconPath = guaranteeIcon(g);
      ti2.description = g.patterns > 1 ? `\xD7 ${g.patterns} patterns` : quarantined ? "quarantined" : g.kind;
      const sample = g.sampleRepr ? `

Example input: \`${g.sampleRepr}\`` : "";
      ti2.tooltip = new import_vscode16.MarkdownString(
        `**\`${g.label}\`** _(${g.kind})_

${g.meaning}${g.reason ? `

${g.reason}` : ""}${sample}`
      );
      ti2.contextValue = quarantined ? "semipy.guaranteeQuarantined" : "semipy.guarantee";
      return ti2;
    }
    if (element.kind === "ledgerGroup") {
      const e = computeSlotInsight(element.slot)?.effect;
      const ti2 = new import_vscode16.TreeItem("Effects", import_vscode16.TreeItemCollapsibleState.Collapsed);
      ti2.iconPath = new import_vscode16.ThemeIcon("zap");
      if (e) {
        const bits = [`${e.applied} applied`];
        if (e.reverted)
          bits.push(`${e.reverted} reverted`);
        if (e.pending)
          bits.push(`${e.pending} pending`);
        ti2.description = bits.join(" \xB7 ");
      }
      return ti2;
    }
    if (element.kind === "event") {
      const e = element.event;
      const ti2 = new import_vscode16.TreeItem(eventLabel(e), import_vscode16.TreeItemCollapsibleState.None);
      ti2.iconPath = eventIcon(e);
      ti2.description = e.status || "applied";
      ti2.contextValue = (e.status || "applied") === "applied" ? "semipy.ledgerEvent" : "semipy.ledgerEventReverted";
      ti2.command = {
        command: "semipy.viewActiveCode",
        title: "View active implementation",
        arguments: [element.slot.slot_id]
      };
      return ti2;
    }
    if (element.kind === "branch") {
      const isDefault = element.branch.name === (element.slot.default_branch || "main");
      const ti2 = new import_vscode16.TreeItem(
        `${element.branch.name}${isDefault ? " (HEAD)" : ""}`,
        import_vscode16.TreeItemCollapsibleState.Collapsed
      );
      ti2.iconPath = new import_vscode16.ThemeIcon("git-branch");
      return ti2;
    }
    const v = orderedVersions(element.slot).find(
      (x) => x.commit.commit_id === element.commit.commit_id
    );
    const verLabel = v ? `v${v.version} \xB7 ` : "";
    const ti = new import_vscode16.TreeItem(
      `${verLabel}${(element.commit.decision || "?").toUpperCase()}`,
      import_vscode16.TreeItemCollapsibleState.None
    );
    const flags = [v?.isActive ? "running" : "", v?.isLocked ? "pinned" : ""].filter(Boolean);
    flags.push(element.commit.commit_id.slice(0, 8));
    if (element.commit.timestamp) {
      flags.push(new Date(element.commit.timestamp * 1e3).toLocaleString());
    }
    ti.description = flags.join(" \xB7 ");
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
      return Object.values(element.portal.slots).filter((slot) => slot.commits && Object.keys(slot.commits).length > 0).map((slot) => ({ kind: "slot", portal: element.portal, slot }));
    }
    if (element.kind === "slot") {
      const out = [];
      const hasContract = !!element.slot.contract?.cases && Object.keys(element.slot.contract.cases).length > 0;
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
      return groupGuarantees(cases).filter((g) => g.patterns > 0 || g.quarantined > 0).map((guarantee) => ({
        kind: "guarantee",
        portal: element.portal,
        slot: element.slot,
        guarantee
      }));
    }
    if (element.kind === "ledgerGroup") {
      const events = element.slot.ledger?.events || [];
      return [...events].reverse().map((event) => ({
        kind: "event",
        portal: element.portal,
        slot: element.slot,
        event
      }));
    }
    if (element.kind === "branch") {
      const chain = walkHistoryCommits(element.slot, element.branch.head);
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
    const portal = element.kind === "portal" ? void 0 : element.portal;
    if (!portal) {
      return void 0;
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
            head: element.commit.commit_id
          }
        };
      default:
        return void 0;
    }
  }
};

// src/features/versionTree/versionActions.ts
var import_child_process = require("child_process");
var fs4 = __toESM(require("fs"));
var path8 = __toESM(require("path"));
var import_vscode17 = require("vscode");
var previewSources = /* @__PURE__ */ new Map();
function setCommitPreviewSource(slotId, commitId, source) {
  const key = `${slotId}:${commitId}`;
  previewSources.set(key, source);
  return import_vscode17.Uri.from({ scheme: "semipy-commit", path: "/preview.py", query: key });
}
function registerCommitTextProvider() {
  return import_vscode17.workspace.registerTextDocumentContentProvider("semipy-commit", {
    provideTextDocumentContent(uri) {
      const key = uri.query;
      return previewSources.get(key) || "# Preview expired; run the tree command again.\n";
    }
  });
}
async function viewGeneratedCode(slotId, commitId, source) {
  const uri = setCommitPreviewSource(slotId, commitId, source);
  const doc = await import_vscode17.workspace.openTextDocument(uri);
  await import_vscode17.window.showTextDocument(doc, { preview: true });
}
function expandWorkspaceVars(s) {
  let out = s;
  const folders = import_vscode17.workspace.workspaceFolders ?? [];
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
  if (!path8.isAbsolute(out)) {
    const wf = import_vscode17.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (wf) {
      out = path8.resolve(wf, out);
    }
  }
  try {
    return path8.normalize(out);
  } catch {
    return out;
  }
}
function pushIfFile(out, p) {
  try {
    if (fs4.existsSync(p) && fs4.statSync(p).isFile()) {
      out.push(p);
    }
  } catch {
  }
}
function candidateVenvInterpreterPaths() {
  const candidates = [];
  const roots = /* @__PURE__ */ new Set();
  for (const wf of import_vscode17.workspace.workspaceFolders ?? []) {
    roots.add(wf.uri.fsPath);
    roots.add(path8.dirname(wf.uri.fsPath));
  }
  for (const root of roots) {
    pushIfFile(candidates, path8.join(root, ".venv", "bin", "python"));
    pushIfFile(candidates, path8.join(root, ".venv", "bin", "python3"));
    pushIfFile(candidates, path8.join(root, "venv", "bin", "python"));
    pushIfFile(candidates, path8.join(root, "venv", "bin", "python3"));
    pushIfFile(candidates, path8.join(root, ".venv", "Scripts", "python.exe"));
    pushIfFile(candidates, path8.join(root, "venv", "Scripts", "python.exe"));
  }
  return candidates;
}
function resolvePythonExecutable() {
  const cfg = import_vscode17.workspace.getConfiguration("semipy");
  const explicit = resolveConfiguredPythonPath((cfg.get("pythonPath") || "").trim());
  if (explicit) {
    return explicit;
  }
  for (const p of candidateVenvInterpreterPaths()) {
    return p;
  }
  const py = import_vscode17.workspace.getConfiguration("python");
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
var fs5 = __toESM(require("fs"));
var path9 = __toESM(require("path"));
var import_vscode18 = require("vscode");
var SemipyDiagnosticManager = class {
  /** semipy `cache_dir` (directory containing `diagnostics.json`). */
  constructor(portalCacheDir) {
    this.portalCacheDir = portalCacheDir;
  }
  collection = import_vscode18.languages.createDiagnosticCollection("semipy");
  dispose() {
    this.collection.dispose();
  }
  refresh() {
    this.collection.clear();
    const cacheDir = this.portalCacheDir();
    if (!cacheDir) {
      return;
    }
    const p = path9.join(cacheDir, "diagnostics.json");
    let data;
    try {
      const raw = fs5.readFileSync(p, "utf8");
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
      const uri = import_vscode18.Uri.file(fp);
      this.collection.set(uri, diags);
    }
  }
  entryToDiagnostic(e) {
    const start = Math.max(1, e.source_line_start) - 1;
    const end = Math.max(1, e.source_line_end) - 1;
    const sev = e.severity === "error" ? import_vscode18.DiagnosticSeverity.Error : e.severity === "warning" ? import_vscode18.DiagnosticSeverity.Warning : import_vscode18.DiagnosticSeverity.Information;
    const d = new import_vscode18.Diagnostic(
      new import_vscode18.Range(new import_vscode18.Position(start, 0), new import_vscode18.Position(end, 2e3)),
      e.message,
      sev
    );
    d.source = "semipy";
    d.code = e.slot_id ? `semi-call-error:${e.slot_id}` : e.code || "semi-call-error";
    d.relatedInformation = [];
    if (e.generated_path && e.generated_line_range?.length === 2) {
      const [a, b] = e.generated_line_range;
      const root = this.portalCacheDir() || "";
      const gp = path9.isAbsolute(e.generated_path) ? e.generated_path : path9.join(root, e.generated_path);
      d.relatedInformation.push({
        location: {
          uri: import_vscode18.Uri.file(gp),
          range: new import_vscode18.Range(
            new import_vscode18.Position(Math.max(1, a) - 1, 0),
            new import_vscode18.Position(Math.max(1, b) - 1, 2e3)
          )
        },
        message: "Generated implementation"
      });
    }
    return d;
  }
};

// src/features/diagnostics/codeActions.ts
var import_vscode19 = require("vscode");
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
      const action = new import_vscode19.CodeAction("Regenerate this spec (semipy CLI)", import_vscode19.CodeActionKind.QuickFix);
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
var import_vscode20 = require("vscode");
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
    if (semiformalLine === void 0 && t.startsWith("@semiformal")) {
      semiformalLine = i;
      break;
    }
    if (defLine === void 0 && (t.startsWith("def ") || t.startsWith("async def"))) {
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
  _onDidChange = new import_vscode20.EventEmitter();
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
      if (!slot.commits || Object.keys(slot.commits).length === 0) {
        continue;
      }
      const spec = slot.slot_spec;
      const ui = resolveSlotUiLines(document, slot);
      const lineIdx = ui?.codeLensLine0 ?? codeLensLineIndexStale(document, spec);
      if (lineIdx === void 0 || lineIdx >= document.lineCount) {
        continue;
      }
      const range = new import_vscode20.Range(lineIdx, 0, lineIdx, 0);
      const insight = computeSlotInsight(slot);
      const headline = insight ? insightChips(insight).join(" \xB7 ") : "Semipy slot";
      out.push(
        new import_vscode20.CodeLens(range, {
          title: headline,
          command: "semipy.inspectSlot",
          arguments: [slot.slot_id]
        })
      );
      const vlabel = versionLensLabel(slot);
      if (vlabel) {
        out.push(
          new import_vscode20.CodeLens(range, {
            title: vlabel,
            command: "semipy.pickSlotVersion",
            arguments: [slot.slot_id]
          })
        );
      }
      if (insight && insight.effect.applied > 0 && insight.effect.latestEventId) {
        out.push(
          new import_vscode20.CodeLens(range, {
            title: "Revert effect",
            command: "semipy.revertEffect",
            arguments: [slot.slot_id, insight.effect.latestEventId]
          })
        );
      }
    }
    return out;
  }
};
var SemipyInlayHintsProvider = class {
  constructor(getPortal, enabled) {
    this.getPortal = getPortal;
    this.enabled = enabled;
  }
  _onDidChange = new import_vscode20.EventEmitter();
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
      if (!slot.commits || Object.keys(slot.commits).length === 0) {
        continue;
      }
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
      const label = ` ${decisionGlyph(commit?.decision || "")} ${decision} \xB7 ${idShort} `;
      hints.push(
        new import_vscode20.InlayHint(
          new import_vscode20.Position(lineNo, line.text.length),
          label,
          import_vscode20.InlayHintKind.Type
        )
      );
    }
    return hints.length ? hints : void 0;
  }
};

// src/features/specCommentSyntax/specCommentSyntaxDecorations.ts
var import_vscode21 = require("vscode");
function createSpecCommentSyntaxTypes() {
  return {
    specMarker: import_vscode21.window.createTextEditorDecorationType({
      fontWeight: "600",
      light: { color: "#008f84" },
      dark: { color: "#4ec9b0" }
    }),
    specBody: import_vscode21.window.createTextEditorDecorationType({
      light: { color: "#007a8a" },
      dark: { color: "#9cdcfe" }
    }),
    reasoningMarker: import_vscode21.window.createTextEditorDecorationType({
      fontWeight: "600",
      light: { color: "#5a8a3d" },
      dark: { color: "#6a9955" }
    }),
    reasoningBody: import_vscode21.window.createTextEditorDecorationType({
      light: { color: "#3d6b2e" },
      dark: { color: "#b5cea8" }
    }),
    reasoningKeyProvenance: import_vscode21.window.createTextEditorDecorationType({
      fontWeight: "600",
      light: { color: "#4a6fa5" },
      dark: { color: "#7fa6d8" }
    }),
    reasoningKeyEffect: import_vscode21.window.createTextEditorDecorationType({
      fontWeight: "600",
      light: { color: "#8a6a2a" },
      dark: { color: "#d7a65f" }
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
        marker: new import_vscode21.Range(new import_vscode21.Position(lineIdx, markerStart), new import_vscode21.Position(lineIdx, markerEnd)),
        body: new import_vscode21.Range(new import_vscode21.Position(lineIdx, markerEnd), new import_vscode21.Position(lineIdx, bodyEnd))
      });
      pos = markerEnd;
      continue;
    }
    if (mLt) {
      const markerStart = gt;
      const markerEnd = gt + mLt[0].length;
      const bodyEnd = line.length;
      reasoning.push({
        marker: new import_vscode21.Range(new import_vscode21.Position(lineIdx, markerStart), new import_vscode21.Position(lineIdx, markerEnd)),
        body: new import_vscode21.Range(new import_vscode21.Position(lineIdx, markerEnd), new import_vscode21.Position(lineIdx, bodyEnd))
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
    editor.setDecorations(types.reasoningKeyProvenance, []);
    editor.setDecorations(types.reasoningKeyEffect, []);
    return;
  }
  const specM = [];
  const specB = [];
  const reasM = [];
  const reasB = [];
  const keyProv = [];
  const keyEff = [];
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
    const steer = parseSteeringLine(line);
    if (steer) {
      const r = new import_vscode21.Range(
        new import_vscode21.Position(lineIdx, steer.keyStart),
        new import_vscode21.Position(lineIdx, steer.keyEnd)
      );
      (steer.zone === "provenance" ? keyProv : keyEff).push(r);
    }
  }
  editor.setDecorations(types.specMarker, specM);
  editor.setDecorations(types.specBody, specB);
  editor.setDecorations(types.reasoningMarker, reasM);
  editor.setDecorations(types.reasoningBody, reasB);
  editor.setDecorations(types.reasoningKeyProvenance, keyProv);
  editor.setDecorations(types.reasoningKeyEffect, keyEff);
}
function disposeSpecCommentSyntaxTypes(types) {
  types.specMarker.dispose();
  types.specBody.dispose();
  types.reasoningMarker.dispose();
  types.reasoningBody.dispose();
  types.reasoningKeyProvenance.dispose();
  types.reasoningKeyEffect.dispose();
}

// src/extension.ts
function semipyCliFailureMessage(stderr, stdout, fallback) {
  let detail = (stderr || stdout || fallback).trim().slice(0, 500);
  if (detail.includes("No module named 'semipy'") || detail.includes("No module named semipy")) {
    detail += " Use Python: Select Interpreter for an environment that includes semipy, or set semipy.pythonPath.";
  }
  return detail;
}
function pickActiveHead(slot) {
  const defaultHead = slot.branches?.[slot.default_branch]?.head;
  if (defaultHead) {
    return defaultHead;
  }
  const heads = Object.values(slot.branches || {}).map((b) => slot.commits[b.head]).filter((c) => !!c);
  heads.sort((a, b) => b.timestamp - a.timestamp);
  return heads[0]?.commit_id;
}
async function rewindSpecIfSnapshot(editor, slot, slotId, commitId, portalRel, workspaceRoot) {
  if (!editor) {
    return;
  }
  const snap = slot?.commits?.[commitId]?.source_snapshot;
  if (!snap?.slot_region_text) {
    return;
  }
  if (editor.document.isDirty) {
    await editor.document.save();
  }
  await runSemipyCli(
    ["rewind-spec", "--portal", portalRel, "--slot-id", slotId, "--commit-id", commitId],
    workspaceRoot
  );
}
function sessionSourceOpts() {
  let raw = import_vscode22.workspace.getConfiguration("semipy").get("sessionSource")?.trim();
  if (raw?.includes("${workspaceFolder}")) {
    const folder = import_vscode22.workspace.workspaceFolders?.[0]?.uri.fsPath;
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
  state.portalCacheDir = path10.dirname(found);
  const wf = import_vscode22.workspace.getWorkspaceFolder(import_vscode22.Uri.file(found));
  state.workspaceRoot = wf?.uri.fsPath ?? path10.dirname(state.portalCacheDir);
}
function activate(context) {
  const portalState = {
    portal: void 0,
    portalPath: void 0,
    portalCacheDir: void 0,
    workspaceRoot: void 0
  };
  const cfg = () => import_vscode22.workspace.getConfiguration("semipy");
  const opacityTypes = createOpacityDecorationTypes();
  const dispatchDimType = createDispatchOpacityType();
  const phraseTypes = createPhraseDecorationTypes();
  const specSyntaxTypes = createSpecCommentSyntaxTypes();
  const gutterTypes = createGutterHealthTypes(context.extensionPath);
  const regressionDiag = new RegressionDiagnosticManager();
  const debounceMs = () => cfg().get("debounceMs") ?? 200;
  const lastHeads = /* @__PURE__ */ new Map();
  let headsSeeded = false;
  const signFlip = new SignFlipCoordinator(
    () => cfg().get("signFlipOnSkeletonEdit") ?? false,
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
  const treeView = import_vscode22.window.createTreeView("semipy.slotHistory", {
    treeDataProvider: tree,
    showCollapseAll: true
  });
  const status = import_vscode22.window.createStatusBarItem(import_vscode22.StatusBarAlignment.Left, 100);
  status.command = "semipy.refreshHistory";
  const modes = import_vscode22.window.createStatusBarItem(import_vscode22.StatusBarAlignment.Left, 99);
  modes.text = "$(settings) Semipy";
  modes.tooltip = "Semipy steering \u2014 enable contract / effect gates (scaffolds configure(...))";
  modes.command = "semipy.steeringModes";
  modes.show();
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
      editor.setDecorations(specSyntaxTypes.reasoningKeyProvenance, []);
      editor.setDecorations(specSyntaxTypes.reasoningKeyEffect, []);
    }
    if (cfg().get("enableGutterHealth") ?? true) {
      refreshGutterHealth(editor, portalState.portal, gutterTypes);
    } else {
      refreshGutterHealth(editor, void 0, gutterTypes);
    }
    if (cfg().get("dimGeneratedCode") ?? true) {
      refreshDispatchOpacity(editor, dispatchDimType);
    } else {
      editor.setDecorations(dispatchDimType, []);
    }
    refreshPhraseDecorations(
      editor,
      portalState.portal,
      cacheDir,
      phraseTypes,
      import_vscode22.workspace.workspaceFolders?.map((w) => w.uri.fsPath) ?? []
    );
    if (portalState.portal) {
      const n = Object.keys(portalState.portal.slots).length;
      status.text = `Semipy: ${n} slot(s)`;
      status.tooltip = portalState.portalPath ? `Portal: ${portalState.portalPath}` : "Semipy portal resolved for this file.";
      status.command = "semipy.refreshHistory";
      status.show();
    } else {
      const doc = editor.document;
      const looksSemiformal = doc.languageId === "python" && /@semiformal|semi\s*\(/.test(doc.getText());
      if (looksSemiformal) {
        status.text = "$(warning) Semipy: no portal";
        status.tooltip = "This file uses @semiformal / semi() but no .semiformal portal was found on the path above it.\nRun the file once to generate it (its cache_dir must resolve to a folder at or above this file -- prefer an absolute path), or set semipy.sessionSource. Click to retry.";
        status.command = "semipy.refreshHistory";
        status.show();
      } else {
        status.hide();
      }
    }
    tree.refresh();
    diag.refresh();
    codeLensProvider.refresh();
    inlayProvider.refresh();
    linked.onSelectionOrPortal(editor, portalState.portal, cacheDir);
    if (cfg().get("notifyOnResolution") ?? true) {
      regressionDiag.refresh(editor, portalState.portal);
    } else {
      regressionDiag.clear();
    }
  }
  async function revealSlot(slotId) {
    const ed = import_vscode22.window.activeTextEditor;
    if (ed) {
      refreshPortalForUri(ed.document.uri.fsPath, portalState);
    }
    tree.refresh();
    const el = tree.slotElement(slotId);
    if (!el) {
      void import_vscode22.window.showWarningMessage("Semipy: that slot is not in the current portal.");
      return;
    }
    try {
      await treeView.reveal(el, { select: true, focus: true, expand: 2 });
    } catch {
    }
  }
  function notifyResolutionChanges(portal) {
    if (!portal || !(cfg().get("notifyOnResolution") ?? true)) {
      return;
    }
    const seenNow = /* @__PURE__ */ new Map();
    const changed = [];
    for (const slot of Object.values(portal.slots)) {
      const commit = activeCommitFromPortalSlot(slot);
      if (!commit) {
        continue;
      }
      seenNow.set(slot.slot_id, commit.commit_id);
      const prev = lastHeads.get(slot.slot_id);
      if (headsSeeded && prev !== void 0 && prev !== commit.commit_id) {
        changed.push(slot);
      }
    }
    lastHeads.clear();
    for (const [k, v] of seenNow) {
      lastHeads.set(k, v);
    }
    headsSeeded = true;
    for (const slot of changed) {
      const insight = computeSlotInsight(slot);
      if (!insight) {
        continue;
      }
      const fn = slot.slot_spec?.enclosing_function_qualname || slot.function_name_base || "slot";
      if (insight.health === "danger") {
        const n = insight.change?.unintended ?? 0;
        void import_vscode22.window.showWarningMessage(
          `Semipy ${insight.decision} ${fn} \u2014 ${n} unintended regression${n === 1 ? "" : "s"}.`,
          "Inspect"
        ).then((pick) => {
          if (pick === "Inspect") {
            void revealSlot(slot.slot_id);
          }
        });
      } else {
        const guarantee = insight.contract.active ? ` \xB7 ${insight.contract.active} guarantee(s) hold` : "";
        import_vscode22.window.setStatusBarMessage(
          `$(sparkle) Semipy ${insight.glyph} ${insight.decision} ${fn}${guarantee}`,
          6e3
        );
      }
    }
  }
  const opacitySub = subscribeOpacityWrapper(opacityTypes, debounceMs, refreshAllDecorations);
  context.subscriptions.push(
    getSemipyOutputChannel(),
    treeView,
    status,
    modes,
    { dispose: () => disposeGutterHealthTypes(gutterTypes) },
    { dispose: () => dispatchDimType.dispose() },
    regressionDiag,
    { dispose: () => disposeSpecCommentSyntaxTypes(specSyntaxTypes) },
    opacitySub,
    signFlip.attach(),
    { dispose: () => linked.dispose() },
    diag,
    import_vscode22.languages.registerCodeLensProvider({ language: "python", scheme: "file" }, codeLensProvider),
    import_vscode22.languages.registerInlayHintsProvider({ language: "python", scheme: "file" }, inlayProvider),
    import_vscode22.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("semipy")) {
        codeLensProvider.refresh();
        inlayProvider.refresh();
        refreshAllDecorations(import_vscode22.window.activeTextEditor);
      }
    }),
    import_vscode22.window.onDidChangeTextEditorSelection((e) => {
      refreshPortalForUri(e.textEditor.document.uri.fsPath, portalState);
      linked.onSelectionOrPortal(e.textEditor, portalState.portal, portalState.portalCacheDir);
    }),
    import_vscode22.window.onDidChangeActiveTextEditor((ed) => {
      if (ed) {
        signFlip.seedDocument(ed.document);
      }
      refreshAllDecorations(ed);
    }),
    import_vscode22.languages.registerHoverProvider(
      { language: "python", scheme: "file" },
      createPhraseHoverProvider(
        () => portalState.portal,
        () => portalState.portalCacheDir,
        () => import_vscode22.workspace.workspaceFolders?.map((w) => w.uri.fsPath) ?? []
      )
    ),
    import_vscode22.languages.registerHoverProvider(
      { language: "python", scheme: "file" },
      createSlotInsightHoverProvider(
        () => portalState.portal,
        () => cfg().get("enableInsightHover") ?? true
      )
    ),
    import_vscode22.languages.registerHoverProvider(
      { language: "python", scheme: "file" },
      createSteeringHoverProvider()
    ),
    import_vscode22.languages.registerCodeActionsProvider(
      { language: "python", scheme: "file" },
      createSteeringCodeActionProvider()
    ),
    import_vscode22.commands.registerCommand("semipy.steeringModes", () => runSteeringModesQuickPick()),
    import_vscode22.commands.registerCommand("semipy.inspectSlot", (slotId) => {
      if (slotId) {
        void revealSlot(slotId);
      }
    }),
    import_vscode22.commands.registerCommand(
      "semipy.relaxGuarantee",
      async (item) => {
        const slotId = item?.slot?.slot_id;
        const caseIds = item?.guarantee?.caseIds ?? [];
        if (!slotId || caseIds.length === 0) {
          void import_vscode22.window.showWarningMessage("Semipy: nothing to relax for this guarantee.");
          return;
        }
        const ed = import_vscode22.window.activeTextEditor;
        if (ed) {
          refreshPortalForUri(ed.document.uri.fsPath, portalState);
        }
        const root = portalState.workspaceRoot;
        const portalPath = portalState.portalPath;
        if (!root || !portalPath) {
          void import_vscode22.window.showErrorMessage("Semipy: no portal for relax.");
          return;
        }
        const ok = await import_vscode22.window.showWarningMessage(
          `Relax guarantee "${item.guarantee?.label}"? It will be quarantined (kept for audit, no longer enforced) across ${caseIds.length} input pattern(s).`,
          { modal: true },
          "Relax"
        );
        if (ok !== "Relax") {
          return;
        }
        const rel = path10.relative(root, portalPath);
        const r = await runSemipyCli(
          ["quarantine-cases", "--portal", rel, "--slot-id", slotId, "--case-ids", caseIds.join(",")],
          root
        );
        if (r.code !== 0 && r.code !== null) {
          void import_vscode22.window.showErrorMessage(
            `Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "relax failed")}`
          );
          return;
        }
        void import_vscode22.window.showInformationMessage((r.stdout || r.stderr || "Guarantee relaxed.").trim().slice(0, 200));
        refreshAllDecorations(import_vscode22.window.activeTextEditor);
      }
    ),
    import_vscode22.commands.registerCommand("semipy.viewActiveCode", async (slotId) => {
      const ed = import_vscode22.window.activeTextEditor;
      const fsPath = ed?.document.uri.fsPath;
      const portalPath = fsPath && findPortalJsonPathForEditor(fsPath, sessionSourceOpts()) || portalState.portalPath;
      const portal = portalPath ? loadPortalJson(portalPath) : portalState.portal;
      const slot = portal?.slots[slotId];
      const commit = slot ? activeCommitFromPortalSlot(slot) : void 0;
      const src = commit?.generated_source;
      if (!slot || !commit || !src) {
        void import_vscode22.window.showWarningMessage("Semipy: no active implementation found for this slot.");
        return;
      }
      await viewGeneratedCode(slotId, commit.commit_id, src);
    }),
    import_vscode22.commands.registerCommand("semipy.revertEffectTreeItem", (item) => {
      if (item?.slot?.slot_id && item.event?.event_id) {
        return import_vscode22.commands.executeCommand("semipy.revertEffect", item.slot.slot_id, item.event.event_id);
      }
      return void 0;
    }),
    import_vscode22.commands.registerCommand("semipy.revertEffect", async (slotId, eventId) => {
      const ed = import_vscode22.window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!root || !portalPath || !slotId || !eventId) {
        void import_vscode22.window.showErrorMessage("Semipy: no portal / event for revert.");
        return;
      }
      const ok = await import_vscode22.window.showWarningMessage(
        `Revert this applied effect? semipy will replay its stored compensations (exact inverse of what was done).`,
        { modal: true },
        "Revert"
      );
      if (ok !== "Revert") {
        return;
      }
      const rel = path10.relative(root, portalPath);
      const r = await runSemipyCli(
        ["revert-effect", "--portal", rel, "--slot-id", slotId, "--event-id", eventId],
        root
      );
      if (r.code !== 0 && r.code !== null) {
        void import_vscode22.window.showErrorMessage(`Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "revert failed")}`);
        return;
      }
      void import_vscode22.window.showInformationMessage((r.stdout || r.stderr || "Effect reverted.").trim().slice(0, 300));
      refreshAllDecorations(import_vscode22.window.activeTextEditor);
    }),
    import_vscode22.commands.registerCommand("semipy.promoteReasoningLine", async (uriArg, line0) => {
      const uri = typeof uriArg === "string" ? import_vscode22.Uri.parse(uriArg) : uriArg;
      const doc = await import_vscode22.workspace.openTextDocument(uri);
      if (line0 < 0 || line0 >= doc.lineCount) {
        return;
      }
      const line = doc.lineAt(line0);
      const fixed = rewriteReasoningPrefixToSpec(line.text);
      if (fixed === null || fixed === line.text) {
        void import_vscode22.window.showInformationMessage("Semipy: that line is not an inferred (#<) note.");
        return;
      }
      const edit = new import_vscode22.WorkspaceEdit();
      edit.replace(uri, line.range, fixed);
      await import_vscode22.workspace.applyEdit(edit);
      import_vscode22.window.setStatusBarMessage("$(pin) Semipy: pinned as contract (#>) \u2014 re-run to honour it.", 5e3);
    }),
    import_vscode22.commands.registerCommand("semipy.dismissReasoningLine", async (uriArg, line0) => {
      const uri = typeof uriArg === "string" ? import_vscode22.Uri.parse(uriArg) : uriArg;
      const doc = await import_vscode22.workspace.openTextDocument(uri);
      if (line0 < 0 || line0 >= doc.lineCount) {
        return;
      }
      const start = new import_vscode22.Position(line0, 0);
      const end = line0 + 1 < doc.lineCount ? new import_vscode22.Position(line0 + 1, 0) : doc.lineAt(line0).range.end;
      const edit = new import_vscode22.WorkspaceEdit();
      edit.delete(uri, new import_vscode22.Range(start, end));
      await import_vscode22.workspace.applyEdit(edit);
    }),
    registerCommitTextProvider(),
    import_vscode22.commands.registerCommand("semipy.noop", () => {
    }),
    import_vscode22.commands.registerCommand("semipy.showOutput", () => {
      getSemipyOutputChannel().show(true);
    }),
    import_vscode22.commands.registerCommand("semipy.openSplitView", async () => {
      const ed = import_vscode22.window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      if (!portalState.portal || !portalState.portalCacheDir) {
        for (const v of import_vscode22.window.visibleTextEditors) {
          if (v.document.languageId !== "python") {
            continue;
          }
          refreshPortalForUri(v.document.uri.fsPath, portalState);
          if (portalState.portal) {
            break;
          }
        }
      }
      if (!portalState.portal || !portalState.portalCacheDir) {
        void import_vscode22.window.showWarningMessage(
          "Semipy: no portal resolved. Focus the Python file that owns this slot, then try again."
        );
        return;
      }
      await openDispatchSplitView(portalState.portalCacheDir, portalState.portal.module_name);
    }),
    import_vscode22.commands.registerCommand("semipy.refreshHistory", () => {
      const ed = import_vscode22.window.activeTextEditor;
      refreshAllDecorations(ed);
      tree.refresh();
    }),
    import_vscode22.commands.registerCommand("semipy.pickSlotVersion", async (slotId) => {
      const ed = import_vscode22.window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      const portal = portalState.portal;
      if (!portal) {
        void import_vscode22.window.showWarningMessage("Semipy: no portal for this file.");
        return;
      }
      const slot = portal.slots[slotId];
      if (!slot) {
        return;
      }
      const versions = orderedVersions(slot);
      if (versions.length === 0) {
        return;
      }
      const isLocked = versions.some((v) => v.isLocked);
      const items = [];
      if (isLocked) {
        items.push({
          label: "$(history) Use latest (unlock)",
          description: "follow the newest version automatically",
          action: "unlock"
        });
      }
      for (const v of [...versions].reverse()) {
        const c = v.commit;
        const flags = [v.isActive ? "running" : "", v.isLocked ? "pinned" : ""].filter(Boolean).join(" \xB7 ");
        items.push({
          label: `${v.isActive ? "$(circle-filled)" : "$(circle-outline)"} v${v.version}  ${(c.decision || "?").toUpperCase()}`,
          description: flags || void 0,
          detail: `${c.commit_id.slice(0, 8)} \xB7 ${new Date(c.timestamp * 1e3).toLocaleString()}${c.message ? " \xB7 " + c.message.slice(0, 40) : ""}`,
          action: "checkout",
          commitId: c.commit_id,
          isActive: v.isActive,
          isLocked: v.isLocked
        });
      }
      const picked = await import_vscode22.window.showQuickPick(items, {
        placeHolder: "Check out a version to run \u2014 pins the chosen version; 'Use latest' follows the newest"
      });
      if (!picked) {
        return;
      }
      if (picked.action === "unlock") {
        await import_vscode22.commands.executeCommand("semipy.unlockSlotVersion", slotId);
        return;
      }
      if (picked.isActive && picked.isLocked) {
        return;
      }
      await import_vscode22.commands.executeCommand("semipy.lockSlotVersion", slotId, picked.commitId);
    }),
    import_vscode22.commands.registerCommand("semipy.lockSlotVersion", async (slotId, commitId) => {
      const ed = import_vscode22.window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!root || !portalPath || !commitId) {
        void import_vscode22.window.showErrorMessage("Semipy: no portal or commit for lock.");
        return;
      }
      const rel = path10.relative(root, portalPath);
      const r = await runSemipyCli(
        ["lock", "--portal", rel, "--slot-id", slotId, "--commit-id", commitId],
        root
      );
      if (r.code !== 0 && r.code !== null) {
        void import_vscode22.window.showErrorMessage(`Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "lock failed")}`);
        return;
      }
      const lockedSlot = portalState.portal?.slots[slotId];
      await rewindSpecIfSnapshot(ed, lockedSlot, slotId, commitId, rel, root);
      void import_vscode22.window.showInformationMessage((r.stderr || r.stdout || "Lock saved.").trim().slice(0, 400));
      refreshAllDecorations(import_vscode22.window.activeTextEditor);
    }),
    import_vscode22.commands.registerCommand("semipy.unlockSlotVersion", async (slotId) => {
      const ed = import_vscode22.window.activeTextEditor;
      if (ed) {
        refreshPortalForUri(ed.document.uri.fsPath, portalState);
      }
      const root = portalState.workspaceRoot;
      const portalPath = portalState.portalPath;
      if (!root || !portalPath) {
        void import_vscode22.window.showWarningMessage("Semipy: no portal for this file.");
        return;
      }
      const rel = path10.relative(root, portalPath);
      const r = await runSemipyCli(["unlock", "--portal", rel, "--slot-id", slotId], root);
      if (r.code !== 0 && r.code !== null) {
        void import_vscode22.window.showErrorMessage(`Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "unlock failed")}`);
        return;
      }
      refreshPortalForUri(ed?.document.uri.fsPath ?? "", portalState);
      const unlockedSlot = portalState.portal?.slots[slotId];
      const activeHead = unlockedSlot ? pickActiveHead(unlockedSlot) : void 0;
      if (activeHead) {
        await rewindSpecIfSnapshot(ed, unlockedSlot, slotId, activeHead, rel, root);
      }
      void import_vscode22.window.showInformationMessage((r.stderr || r.stdout || "Unlocked.").trim().slice(0, 400));
      refreshAllDecorations(import_vscode22.window.activeTextEditor);
    }),
    import_vscode22.commands.registerCommand(
      "semipy.viewGeneratedCode",
      async (slotId, commitId) => {
        const ed = import_vscode22.window.activeTextEditor;
        const fsPath = ed?.document.uri.fsPath;
        const portalPath = fsPath && findPortalJsonPathForEditor(fsPath, sessionSourceOpts()) || portalState.portalPath;
        const portal = portalPath ? loadPortalJson(portalPath) : portalState.portal;
        const slot = portal?.slots[slotId];
        const src = slot?.commits[commitId]?.generated_source;
        if (!src) {
          void import_vscode22.window.showWarningMessage(
            "Semipy: commit source not loaded. Refresh history or open the source file that owns this portal."
          );
          return;
        }
        await viewGeneratedCode(slotId, commitId, src);
      }
    ),
    import_vscode22.commands.registerCommand(
      "semipy.regenerateSlotDiagnostic",
      async (ws, portalRel, slotId) => {
        const r = await runSemipyCli(
          ["regenerate", "--portal", portalRel, "--slot-id", slotId],
          ws
        );
        if (r.code !== 0 && r.code !== null) {
          void import_vscode22.window.showErrorMessage(
            `Semipy: ${semipyCliFailureMessage(r.stderr, r.stdout, "regenerate failed")}`
          );
          return;
        }
        void import_vscode22.window.showInformationMessage(r.stderr || r.stdout || "semipy regenerate finished.");
        diag.refresh();
      }
    ),
    import_vscode22.languages.registerCodeActionsProvider(
      { language: "python", scheme: "file" },
      createRegenerateCodeActionProvider(
        () => portalState.workspaceRoot,
        () => portalState.portalPath && portalState.workspaceRoot ? path10.relative(portalState.workspaceRoot, portalState.portalPath) : void 0
      )
    )
  );
  const ed0 = import_vscode22.window.activeTextEditor;
  if (ed0) {
    signFlip.seedDocument(ed0.document);
  }
  refreshAllDecorations(ed0);
  notifyResolutionChanges(portalState.portal);
  if (import_vscode22.workspace.workspaceFolders?.length) {
    const wf = import_vscode22.workspace.workspaceFolders[0].uri.fsPath;
    let timer;
    const fire = () => {
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(() => {
        timer = void 0;
        refreshAllDecorations(import_vscode22.window.activeTextEditor);
        notifyResolutionChanges(portalState.portal);
      }, debounceMs());
    };
    const wPortal = import_vscode22.workspace.createFileSystemWatcher(new import_vscode22.RelativePattern(wf, "**/*.portal.json"));
    const wSemi = import_vscode22.workspace.createFileSystemWatcher(new import_vscode22.RelativePattern(wf, "**/*.semi.py"));
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
    onRefresh(import_vscode22.window.activeTextEditor);
  };
  const sub1 = import_vscode22.window.onDidChangeActiveTextEditor(() => {
    tick();
  });
  const sub2 = import_vscode22.workspace.onDidChangeTextDocument((ev) => {
    const ed = import_vscode22.window.activeTextEditor;
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
