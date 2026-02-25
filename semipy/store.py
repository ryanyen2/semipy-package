"""Portal persistence and dispatch module read/write."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

from semipy.dag import Branch, Commit, Portal, Slot


def _source_with_function_name(source: str, fn_name: str) -> str:
    """Rename the first function definition in source to fn_name so dispatch lookup works."""
    return re.sub(r"\bdef\s+\w+\s*\(", f"def {fn_name}(", source.strip(), count=1)


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
    )


def _portal_to_dict(p: Portal) -> dict[str, Any]:
    return {
        "session_id": p.session_id,
        "source_file": p.source_file,
        "module_name": p.module_name,
        "slots": {sid: _slot_to_dict(s) for sid, s in p.slots.items()},
    }


def _portal_from_dict(d: dict[str, Any]) -> Portal:
    slots = {sid: _slot_from_dict(sd) for sid, sd in d.get("slots", {}).items()}
    return Portal(
        session_id=d.get("session_id", ""),
        source_file=d.get("source_file", ""),
        module_name=d.get("module_name", ""),
        slots=slots,
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
    base = slot.function_name_base
    short = commit.commit_id[:8]
    return f"{base}_{short}"


def write_dispatch_module(cache_dir: Path, portal: Portal) -> tuple[Path, dict[str, tuple[int, int]]]:
    """Write dispatch module with only functions referenced by refs. Returns (path, fn_line_map)."""
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
        refd_commit_ids = set(slot.refs.values())
        for commit_id in refd_commit_ids:
            commit = slot.commits.get(commit_id)
            if commit is None:
                continue
            fn_name = function_name_for_commit(slot, commit)
            lines.append(f"# slot: {slot.function_name_base} | commit: {commit_id[:8]} | {commit.decision}")
            start_line = len(lines) + 1
            fn_source = _source_with_function_name(commit.generated_source, fn_name)
            fn_lines = fn_source.splitlines()
            lines.extend(fn_lines)
            lines.append("")
            end_line = len(lines)
            fn_line_map[fn_name] = (start_line, end_line)
        for usage_id, commit_id in slot.refs.items():
            commit = slot.commits.get(commit_id)
            if commit is not None:
                fn_name = function_name_for_commit(slot, commit)
                dispatch_entries.append(f"DISPATCH[{repr(usage_id)}] = {repr(fn_name)}")

    if dispatch_entries:
        lines.append("")
        lines.extend(dispatch_entries)

    path.write_text("\n".join(lines), encoding="utf-8")
    return path, fn_line_map


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
    if key not in module_cache:
        try:
            ns: dict[str, Any] = {}
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
            module_cache[key] = ns
        except Exception:
            return None
    ns = module_cache[key]
    fn = ns.get(function_name)
    return fn if callable(fn) and not isinstance(fn, type) else None
