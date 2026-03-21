"""Portal persistence and dispatch module read/write."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

from semipy.history import Branch, Commit, Portal, Slot, most_recent_branch_head


def _source_with_function_name(source: str, fn_name: str) -> str:
    """Rename the first function definition in source to fn_name so dispatch lookup works."""
    s = source.strip()

    # Handle both `def` and `async def` emitted by the model.
    def _repl(m: re.Match[str]) -> str:
        async_kw = m.group(1) or ""
        return f"{async_kw}def {fn_name}("

    return re.sub(r"\b(async\s+)?def\s+\w+\s*\(", _repl, s, count=1)


def _dispatch_source_only(source: str) -> str:
    """
    Return only the first top-level function definition (and any leading imports).
    Drops gist footers (e.g. result = fn(...); print(result)) so the dispatch module
    does not reference the original function name after renaming.
    """
    raw = source.strip()
    if not raw:
        return raw
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        return raw
    lines = raw.splitlines()
    end_offset = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            end_offset = getattr(node, "end_lineno", node.lineno)
            break
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            end_offset = getattr(node, "end_lineno", node.lineno)
    if end_offset == 0:
        return raw
    return "\n".join(lines[:end_offset])


def _portal_path(cache_dir: Path, session_id: str) -> Path:
    return cache_dir / f"{session_id}.portal.json"


def _dispatch_dir(cache_dir: Path) -> Path:
    return cache_dir / "runtime"


def _dispatch_module_path(cache_dir: Path, module_name: str) -> Path:
    return _dispatch_dir(cache_dir) / f"{module_name}.semi.py"


def _commit_to_dict(c: Commit) -> dict[str, Any]:
    return {
        "commit_id": c.commit_id,
        "parent_ids": list(c.parent_ids),
        "generated_source": c.generated_source,
        "source_hash": c.source_hash,
        "template_fingerprint": c.template_fingerprint,
        "constants_snapshot": [list(p) for p in c.constants_snapshot],
        "operation_signature": c.operation_signature,
        "prompt_snapshot": c.prompt_snapshot,
        "timestamp": c.timestamp,
        "message": c.message,
        "decision": c.decision,
        "usage_id": c.usage_id or "",
        "runtime_input_fingerprint": getattr(c, "runtime_input_fingerprint", "") or "",
    }


def _commit_from_dict(d: dict[str, Any]) -> Commit:
    return Commit(
        commit_id=d["commit_id"],
        parent_ids=tuple(d.get("parent_ids", [])),
        generated_source=d["generated_source"],
        source_hash=d["source_hash"],
        template_fingerprint=d["template_fingerprint"],
        constants_snapshot=tuple(tuple(p) for p in d.get("constants_snapshot", [])),
        operation_signature=d["operation_signature"],
        prompt_snapshot=d.get("prompt_snapshot", ""),
        timestamp=float(d.get("timestamp", 0)),
        message=d.get("message", ""),
        decision=d.get("decision", "GENERATE"),
        usage_id=d.get("usage_id", "") or "",
        runtime_input_fingerprint=str(d.get("runtime_input_fingerprint", "") or ""),
    )


def _slot_to_dict(s: Slot) -> dict[str, Any]:
    return {
        "slot_id": s.slot_id,
        "call_site_info": s.call_site_info,
        "function_name_base": s.function_name_base,
        "commits": {cid: _commit_to_dict(c) for cid, c in s.commits.items()},
        "branches": {n: {"name": b.name, "head": b.head} for n, b in s.branches.items()},
        "refs": dict(s.refs),
        "default_branch": s.default_branch,
        "upstream_slot_refs": list(s.upstream_slot_refs),
        "spec_hash": s.spec_hash,
        "slot_spec": s.slot_spec,
        "enclosing_function_site_id": s.enclosing_function_site_id,
        "advisor_state": dict(getattr(s, "advisor_state", {}) or {}),
        "input_observation_samples": dict(getattr(s, "input_observation_samples", {}) or {}),
    }


def _slot_from_dict(d: dict[str, Any]) -> Slot:
    commits = {cid: _commit_from_dict(cd) for cid, cd in d.get("commits", {}).items()}
    branches = {
        n: Branch(name=b["name"], head=b["head"])
        for n, b in d.get("branches", {}).items()
    }
    return Slot(
        slot_id=d["slot_id"],
        call_site_info=d.get("call_site_info", {}),
        function_name_base=d["function_name_base"],
        commits=commits,
        branches=branches,
        refs=dict(d.get("refs", {})),
        default_branch=d.get("default_branch", "main"),
        upstream_slot_refs=[tuple(p) for p in d.get("upstream_slot_refs", [])],
        spec_hash=d.get("spec_hash", ""),
        slot_spec=d.get("slot_spec", None),
        enclosing_function_site_id=d.get("enclosing_function_site_id", None),
        advisor_state=dict(d.get("advisor_state", {}) or {}),
        input_observation_samples=dict(d.get("input_observation_samples", {}) or {}),
    )


def _portal_to_dict(p: Portal) -> dict[str, Any]:
    return {
        "session_id": p.session_id,
        "source_file": p.source_file,
        "module_name": p.module_name,
        "slots": {sid: _slot_to_dict(s) for sid, s in p.slots.items()},
        "spec_map": dict(p.spec_map),
        "enclosing_function_slots": dict(p.enclosing_function_slots),
    }


def _portal_from_dict(d: dict[str, Any]) -> Portal:
    slots = {sid: _slot_from_dict(sd) for sid, sd in d.get("slots", {}).items()}
    return Portal(
        session_id=d.get("session_id", ""),
        source_file=d.get("source_file", ""),
        module_name=d.get("module_name", ""),
        slots=slots,
        spec_map=dict(d.get("spec_map", {})),
        enclosing_function_slots=dict(d.get("enclosing_function_slots", {})),
    )


def load_portal(cache_dir: Path, session_id: str, source_file: str, module_name: str) -> Portal:
    """Load portal from JSON or return a new empty Portal if the file is missing or invalid."""
    path = _portal_path(cache_dir, session_id)
    if not path.exists():
        return Portal(
            session_id=session_id,
            source_file=source_file,
            module_name=module_name,
        )
    try:
        with open(path) as f:
            data = json.load(f)
        return _portal_from_dict(data)
    except Exception:
        return Portal(
            session_id=session_id,
            source_file=source_file,
            module_name=module_name,
        )


def save_portal(cache_dir: Path, portal: Portal) -> None:
    """Persist portal to JSON in the cache directory."""
    path = _portal_path(cache_dir, portal.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _portal_to_dict(portal)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def function_name_for_commit(slot: Slot, commit: Commit) -> str:
    """Function name in dispatch module: base_commitid (8 chars)."""
    base = slot.function_name_base or "slot"
    # Sanitize in case base contains notebook placeholders like "<lambda>".
    base = base.strip().replace("<", "").replace(">", "")
    base = re.sub(r"[^0-9a-zA-Z_]", "_", base)
    if not base:
        base = "slot"
    if base[0].isdigit():
        base = "_" + base
    short = commit.commit_id[:8]
    return f"{base}_{short}"


def _get_active_commit(slot: Slot) -> Optional[Commit]:
    """Return the active commit for the slot: most recent branch head, falling back to most recent ref'd commit."""
    c = most_recent_branch_head(slot)
    if c is not None:
        return c
    if slot.refs:
        commit_ids = set(slot.refs.values())
        candidates = [slot.commits[cid] for cid in commit_ids if slot.commits.get(cid)]
        if candidates:
            return max(candidates, key=lambda c: c.timestamp)
    return None


def write_dispatch_module(cache_dir: Path, portal: Portal) -> tuple[Path, dict[str, tuple[int, int]]]:
    """
    Write dispatch module with one implementation per slot (active commit).

    Dispatch format (new):
      DISPATCH = { "<slot_id>": "<function_name>" }

    Also populates `portal.spec_map[slot_id] = "<function_name>:<start>-<end>"`.
    Returns (path, fn_line_map).
    """
    path = _dispatch_module_path(cache_dir, portal.module_name)
    _dispatch_dir(cache_dir).mkdir(parents=True, exist_ok=True)

    lines = [
        f'"""Generated implementations for session {portal.module_name}. Do not edit by hand."""',
        "from __future__ import annotations",
        "",
        "DISPATCH = {}",
        "",
    ]
    dispatch_entries: list[str] = []
    fn_line_map: dict[str, tuple[int, int]] = {}

    for slot in portal.slots.values():
        active = _get_active_commit(slot)
        if active is None:
            continue
        fn_name = function_name_for_commit(slot, active)
        slot_cat = None
        slot_spec = getattr(slot, "slot_spec", None)
        if isinstance(slot_spec, dict):
            slot_cat = slot_spec.get("expected_category")
        spec_preview = ""
        if isinstance(slot_spec, dict):
            spec_preview = (slot_spec.get("spec_text") or "").replace("\n", " ")
            spec_preview = spec_preview[:200] + ("..." if len(spec_preview) > 200 else "")
        lines.append(
            f"# slot: {slot.slot_id} | category: {slot_cat or 'unknown'} | commit: {active.commit_id[:8]} | {active.decision} | spec: {spec_preview}"
        )
        start_line = len(lines) + 1
        source_only = _dispatch_source_only(active.generated_source)
        fn_source = _source_with_function_name(source_only, fn_name)
        fn_lines = fn_source.splitlines()
        lines.extend(fn_lines)
        lines.append("")
        end_line = len(lines)
        fn_line_map[fn_name] = (start_line, end_line)
        dispatch_entries.append(f"DISPATCH[{repr(slot.slot_id)}] = {repr(fn_name)}")
        portal.spec_map[slot.slot_id] = f"{fn_name}:{start_line}-{end_line}"

    if dispatch_entries:
        lines.append("")
        lines.extend(dispatch_entries)

    path.write_text("\n".join(lines), encoding="utf-8")
    return path, fn_line_map


def get_spec_map_entry(portal: Portal, slot_id: str) -> Optional[str]:
    """Return the spec_map entry for `slot_id` if present."""
    return portal.spec_map.get(slot_id)


def get_dispatch_function_line_range(dispatch_path: Path, function_name: str) -> tuple[int, int]:
    """Return (start_line, end_line) for the function in the dispatch module, or (0, 0)."""
    if not dispatch_path.exists():
        return (0, 0)
    content = dispatch_path.read_text(encoding="utf-8")
    file_lines = content.splitlines()
    pattern = f"def {re.escape(function_name)}("
    start = 0
    for i, line in enumerate(file_lines, 1):
        if pattern in line and line.strip().startswith("def "):
            start = i
            break
    if start == 0:
        return (0, 0)
    indent = len(file_lines[start - 1]) - len(file_lines[start - 1].lstrip())
    end_line = len(file_lines)
    for j in range(start, len(file_lines)):
        line = file_lines[j]
        if line.strip() and line.strip().startswith("def ") and (len(line) - len(line.lstrip())) <= indent:
            end_line = j
            break
        end_line = j + 1
    return (start, end_line)


def load_function_from_dispatch(
    cache_dir: Path,
    module_name: str,
    function_name: str,
    module_cache: dict[str, dict[str, Any]],
) -> Optional[Callable[..., Any]]:
    """Load a generated function by name from the dispatch module; uses module_cache to avoid re-executing the file."""
    path = _dispatch_module_path(cache_dir, module_name)
    if not path.exists():
        return None
    key = module_name
    def _load() -> dict[str, Any]:
        ns: dict[str, Any] = {}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
        return ns

    if key not in module_cache:
        try:
            module_cache[key] = _load()
        except Exception:
            return None

    ns = module_cache[key]
    fn = ns.get(function_name)
    if callable(fn) and not isinstance(fn, type):
        return fn

    # Dispatch module may have been updated after the namespace was cached.
    # If the requested function isn't present, reload once from disk.
    try:
        module_cache[key] = _load()
    except Exception:
        return None
    ns = module_cache[key]
    fn2 = ns.get(function_name)
    return fn2 if callable(fn2) and not isinstance(fn2, type) else None


def load_function_from_dispatch_by_slot_id(
    cache_dir: Path,
    module_name: str,
    slot_id: str,
    module_cache: dict[str, dict[str, Any]],
) -> Optional[Callable[..., Any]]:
    """
    More robust dispatch lookup: load the module namespace and resolve
    the function name via DISPATCH[slot_id].
    """
    path = _dispatch_module_path(cache_dir, module_name)
    if not path.exists():
        return None

    key = module_name

    def _load() -> dict[str, Any]:
        ns: dict[str, Any] = {}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
        return ns

    if key not in module_cache:
        try:
            module_cache[key] = _load()
        except Exception:
            return None

    ns = module_cache[key]
    dispatch = ns.get("DISPATCH", None)
    if not isinstance(dispatch, dict):
        # reload once in case the module changed
        try:
            module_cache[key] = _load()
        except Exception:
            return None
        ns = module_cache[key]
        dispatch = ns.get("DISPATCH", None)

    if not isinstance(dispatch, dict):
        return None

    fn_name = dispatch.get(slot_id)
    if not fn_name or not isinstance(fn_name, str):
        return None
    fn = ns.get(fn_name)
    return fn if callable(fn) and not isinstance(fn, type) else None
