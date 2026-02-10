"""Persistent file cache for generated semi() functions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Optional

from semipy.types import CacheEntry


def _compile_source(source: str) -> Callable[..., Any]:
    """Compile generated source into a callable. Expects a single function def."""
    ns: dict[str, Any] = {}
    exec(compile(source, "<generated>", "exec"), ns)
    fns = [v for v in ns.values() if callable(v) and not isinstance(v, type)]
    if not fns:
        raise ValueError("Generated source did not define a callable")
    return fns[0]


def _type_to_key(t: type) -> str:
    if t is type(None):
        return "None"
    return getattr(t, "__qualname__", str(t))


class SemiCache:
    """File-based cache for generated functions. In-memory lookup with disk persistence."""

    def __init__(self, cache_dir: Path):
        self._cache_dir = Path(cache_dir)
        self._memory: dict[tuple[str, str], CacheEntry] = {}

    def _site_dir(self, site_id: str) -> Path:
        d = self._cache_dir / site_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _entry_paths(self, site_id: str, template_hash: str) -> tuple[Path, Path]:
        base = self._site_dir(site_id)
        return base / f"{template_hash}.json", base / f"{template_hash}.py"

    def _load_entry(self, site_id: str, template_hash: str) -> Optional[CacheEntry]:
        json_path, py_path = self._entry_paths(site_id, template_hash)
        if not json_path.exists() or not py_path.exists():
            return None
        try:
            with open(json_path) as f:
                meta = json.load(f)
            with open(py_path) as f:
                source = f.read()
            fn = _compile_source(source)
            return CacheEntry(
                site_id=site_id,
                template_hash=template_hash,
                generated_source=source,
                compiled_fn=fn,
                expected_type=type(None),
            )
        except Exception:
            return None

    def get(self, site_id: str, template_hash: str) -> Optional[CacheEntry]:
        key = (site_id, template_hash)
        if key in self._memory:
            return self._memory[key]
        entry = self._load_entry(site_id, template_hash)
        if entry:
            self._memory[key] = entry
        return entry

    def put(self, entry: CacheEntry) -> None:
        if entry.compiled_fn is None:
            return
        key = (entry.site_id, entry.template_hash)
        self._memory[key] = entry
        json_path, py_path = self._entry_paths(entry.site_id, entry.template_hash)
        with open(py_path, "w") as f:
            f.write(entry.generated_source)
        meta = {
            "site_id": entry.site_id,
            "template_hash": entry.template_hash,
            "expected_type": _type_to_key(entry.expected_type),
        }
        with open(json_path, "w") as f:
            json.dump(meta, f, indent=2)

    def invalidate_site(self, site_id: str) -> None:
        to_drop = [k for k in self._memory if k[0] == site_id]
        for k in to_drop:
            del self._memory[k]
        site_path = self._cache_dir / site_id
        if site_path.exists():
            for f in site_path.iterdir():
                f.unlink()
            site_path.rmdir()


def build_template_hash(template_parts: list[Any], constant_values: dict[str, Any]) -> str:
    """Build a stable hash for cache key from template structure and constant values."""
    import json
    try:
        const_ser = json.dumps(constant_values, sort_keys=True, default=repr)
    except Exception:
        const_ser = repr(sorted(constant_values.items()))
    parts_ser = json.dumps([(p.is_literal, p.value) for p in template_parts], sort_keys=True)
    raw = f"{parts_ser}|{const_ser}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
