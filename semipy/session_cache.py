"""Session-scoped cache: one implementation per semicode per session, structural reuse."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Optional

from semipy.template import structural_fingerprint, template_tree_from_prompt
from semipy.types import (
    CacheEntry,
    PromptTemplate,
    SemiCallSite,
    SessionIndex,
    SemicodeEntry,
    Usage,
    session_id_from_filename,
    session_module_name_from_filename,
)


def _index_path(cache_dir: Path, session_id: str) -> Path:
    return cache_dir / f"{session_id}.index.json"


def _index_automerge_path(cache_dir: Path, session_id: str) -> Path:
    return cache_dir / f"{session_id}.index.automerge"


def _load_session_index_automerge(cache_dir: Path, session_id: str, source_file: str) -> Optional[SessionIndex]:
    """Load session index from Automerge binary if available. Returns None on failure or if automerge not installed."""
    try:
        from automerge.core import Document, ROOT, ScalarType
    except ImportError:
        return None
    path = _index_automerge_path(cache_dir, session_id)
    if not path.exists():
        return None
    try:
        doc = Document.load(path.read_bytes())
        raw = getattr(doc, "get", lambda *a: None)(ROOT, "data")
        if raw is None and hasattr(doc, "get_all"):
            all_vals = doc.get_all(ROOT, "data")
            raw = all_vals[0] if all_vals else None
        if raw is None:
            return None
        data = json.loads(raw)
    except Exception:
        return None
    semicodes = [
        SemicodeEntry(
            semicode_id=e["semicode_id"],
            implementation_id=e["implementation_id"],
            usage_ids=e.get("usage_ids", []),
            function_name=e.get("function_name", ""),
            param_names=e.get("param_names", []),
            expected_type=type(None),
            template_fingerprint=e.get("template_fingerprint", ""),
            usage_count=e.get("usage_count", 0),
            last_validated_at=e.get("last_validated_at"),
            generated_source=e.get("generated_source", ""),
        )
        for e in data.get("semicodes", [])
    ]
    return SessionIndex(
        session_id=data.get("session_id", session_id),
        source_file=data.get("source_file", source_file),
        module_name=data.get("module_name", session_module_name_from_filename(source_file)),
        semicodes=semicodes,
        last_source_fingerprint=data.get("last_source_fingerprint"),
    )


def _save_session_index_automerge(cache_dir: Path, index: SessionIndex) -> bool:
    """Save session index to Automerge binary. Returns True if saved, False if automerge not installed or error."""
    try:
        from automerge.core import Document, ROOT, ScalarType
    except ImportError:
        return False
    path = _index_automerge_path(cache_dir, index.session_id)
    data = {
        "session_id": index.session_id,
        "source_file": index.source_file,
        "module_name": index.module_name,
        "last_source_fingerprint": index.last_source_fingerprint,
        "semicodes": [
            {
                "semicode_id": e.semicode_id,
                "implementation_id": e.implementation_id,
                "usage_ids": e.usage_ids,
                "function_name": e.function_name,
                "param_names": e.param_names,
                "template_fingerprint": e.template_fingerprint,
                "usage_count": e.usage_count,
                "last_validated_at": e.last_validated_at,
                "generated_source": e.generated_source,
            }
            for e in index.semicodes
        ],
    }
    try:
        doc = Document()
        with doc.transaction() as tx:
            tx.put(ROOT, "data", ScalarType.Str, json.dumps(data))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(doc.save())
        return True
    except Exception:
        return False


def load_session_index(cache_dir: Path, session_id: str, source_file: str) -> SessionIndex:
    """Load session index from disk (Automerge binary if present and available, else JSON) or return an empty one."""
    loaded = _load_session_index_automerge(cache_dir, session_id, source_file)
    if loaded is not None:
        return loaded
    path = _index_path(cache_dir, session_id)
    if not path.exists():
        return SessionIndex(
            session_id=session_id,
            source_file=source_file,
            module_name=session_module_name_from_filename(source_file),
        )
    try:
        with open(path) as f:
            data = json.load(f)
        semicodes = [
            SemicodeEntry(
                semicode_id=e["semicode_id"],
                implementation_id=e["implementation_id"],
                usage_ids=e.get("usage_ids", []),
                function_name=e.get("function_name", ""),
                param_names=e.get("param_names", []),
                expected_type=type(None),
                template_fingerprint=e.get("template_fingerprint", ""),
                usage_count=e.get("usage_count", 0),
                last_validated_at=e.get("last_validated_at"),
                generated_source=e.get("generated_source", ""),
            )
            for e in data.get("semicodes", [])
        ]
        return SessionIndex(
            session_id=data.get("session_id", session_id),
            source_file=data.get("source_file", source_file),
            module_name=data.get("module_name", session_module_name_from_filename(source_file)),
            semicodes=semicodes,
            last_source_fingerprint=data.get("last_source_fingerprint"),
        )
    except Exception:
        return SessionIndex(
            session_id=session_id,
            source_file=source_file,
            module_name=session_module_name_from_filename(source_file),
        )


def save_session_index(cache_dir: Path, index: SessionIndex) -> None:
    """Persist session index to disk."""
    path = _index_path(cache_dir, index.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "session_id": index.session_id,
        "source_file": index.source_file,
        "module_name": index.module_name,
        "last_source_fingerprint": index.last_source_fingerprint,
        "semicodes": [
            {
                "semicode_id": e.semicode_id,
                "implementation_id": e.implementation_id,
                "usage_ids": e.usage_ids,
                "function_name": e.function_name,
                "param_names": e.param_names,
                "template_fingerprint": e.template_fingerprint,
                "usage_count": e.usage_count,
                "last_validated_at": e.last_validated_at,
                "generated_source": e.generated_source,
            }
            for e in index.semicodes
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    _save_session_index_automerge(cache_dir, index)


def _entry_module_path(cache_dir: Path, module_name: str) -> Path:
    """Path to the session entry module (e.g. data_wrangling.semi.py)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{module_name}.semi.py"


def _write_entry_module(cache_dir: Path, index: SessionIndex) -> None:
    """Write the session entry module with all generated functions and a DISPATCH map."""
    path = _entry_module_path(cache_dir, index.module_name)
    lines = [
        '"""Generated semiformal implementations for session %s. Do not edit by hand."""' % index.module_name,
        "from __future__ import annotations",
        "",
        "# DISPATCH: usage_id -> function name for lookup",
        "DISPATCH = {}",
        "",
    ]
    dispatch_entries: list[str] = []
    for se in index.semicodes:
        if not se.function_name or not se.generated_source.strip():
            continue
        for uid in se.usage_ids:
            dispatch_entries.append(f'DISPATCH[{repr(uid)}] = {repr(se.function_name)}')
        lines.append("")
        lines.append("# " + se.function_name)
        lines.append(se.generated_source.strip())
        lines.append("")
    if dispatch_entries:
        lines.append("")
        lines.extend(dispatch_entries)
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_function_from_entry_module(
    cache_dir: Path,
    module_name: str,
    function_name: str,
    entry_globals_cache: dict[str, dict[str, Any]],
) -> Optional[Callable[..., Any]]:
    """Load a single function by name from the session entry module. Caches module globals."""
    path = _entry_module_path(cache_dir, module_name)
    if not path.exists():
        return None
    cache_key = module_name
    if cache_key not in entry_globals_cache:
        try:
            ns: dict[str, Any] = {}
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
            entry_globals_cache[cache_key] = ns
        except Exception:
            return None
    ns = entry_globals_cache[cache_key]
    fn = ns.get(function_name)
    return fn if callable(fn) and not isinstance(fn, type) else None


class SessionCache:
    """
    Session-scoped cache: resolve usage to semicode (exact or structural match),
    one implementation per semicode. Entry module per session is the single source of truth.
    """

    def __init__(self, cache_dir: Path):
        self._cache_dir = Path(cache_dir)
        self._memory: dict[str, SessionIndex] = {}
        self._entry_globals_cache: dict[str, dict[str, Any]] = {}

    def _get_index(self, session_id: str, source_file: str) -> SessionIndex:
        key = session_id
        if key not in self._memory:
            self._memory[key] = load_session_index(self._cache_dir, session_id, source_file)
        return self._memory[key]

    def _entry_module_display_path(self, module_name: str) -> str:
        return str(_entry_module_path(self._cache_dir, module_name))

    def get_entry_for_usage(self, usage: Usage) -> Optional[CacheEntry]:
        """
        Resolve usage to a cached implementation: exact usage_id match, or structural
        (same template fingerprint) match. Load from entry module.
        """
        session_id = session_id_from_filename(usage.call_site.filename)
        index = self._get_index(session_id, usage.call_site.filename)
        tree = template_tree_from_prompt(usage.template)
        fingerprint = structural_fingerprint(tree)

        semicode = index.semicode_by_usage_id(usage.usage_id())
        if semicode is None:
            semicode = index.semicode_by_structural_fingerprint(fingerprint)
        if semicode is None:
            return None

        if not semicode.function_name:
            return None
        fn = _load_function_from_entry_module(
            self._cache_dir,
            index.module_name,
            semicode.function_name,
            self._entry_globals_cache,
        )
        if fn is None:
            return None
        display_path = self._entry_module_display_path(index.module_name)
        return CacheEntry(
            generated_source=semicode.generated_source or "",
            compiled_fn=fn,
            cache_display_path=display_path,
        )

    def resolve_or_register(
        self,
        usage: Usage,
        entry: CacheEntry,
        function_name: str,
    ) -> SemicodeEntry:
        """
        If this usage already maps to a semicode (exact or structural), return that entry.
        Otherwise register a new semicode with the given entry's source and return the new SemicodeEntry.
        """
        session_id = session_id_from_filename(usage.call_site.filename)
        index = self._get_index(session_id, usage.call_site.filename)
        tree = template_tree_from_prompt(usage.template)
        fingerprint = structural_fingerprint(tree)

        existing = index.semicode_by_usage_id(usage.usage_id())
        if existing is not None:
            return existing
        existing = index.semicode_by_structural_fingerprint(fingerprint)
        if existing is not None:
            uid = usage.usage_id()
            if uid not in existing.usage_ids:
                existing.usage_ids.append(uid)
            existing.usage_count = len(existing.usage_ids)
            save_session_index(self._cache_dir, index)
            _write_entry_module(self._cache_dir, index)
            self._entry_globals_cache.pop(index.module_name, None)
            return existing

        semicode_id = fingerprint
        implementation_id = hashlib.sha256(entry.generated_source.encode()).hexdigest()[:16]
        new_entry = SemicodeEntry(
            semicode_id=semicode_id,
            implementation_id=implementation_id,
            usage_ids=[usage.usage_id()],
            function_name=function_name,
            param_names=usage.template.variable_names,
            expected_type=type(None),
            template_fingerprint=fingerprint,
            usage_count=1,
            generated_source=entry.generated_source,
        )
        index.semicodes.append(new_entry)
        save_session_index(self._cache_dir, index)
        _write_entry_module(self._cache_dir, index)
        self._entry_globals_cache.pop(index.module_name, None)
        return new_entry

    def invalidate_session(self, session_id: str) -> None:
        """Drop in-memory index for session; does not delete index file or implementations."""
        if session_id in self._memory:
            del self._memory[session_id]


def usage_from_spec(
    call_site: SemiCallSite,
    template: PromptTemplate,
    constant_values: dict[str, Any],
) -> Usage:
    """Build a Usage from call site, template, and constant values."""
    return Usage(
        call_site=call_site,
        template=template,
        constant_values=constant_values or {},
    )


def readable_function_name(call_site: SemiCallSite, slot: int = 0) -> str:
    """Derive a readable function name for the entry module (e.g. frame_filter_0)."""
    base = (call_site.func_qualname or "fn").replace(".", "_").replace(" ", "_")
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in base)
    return f"{safe}_{slot}" if slot else safe
