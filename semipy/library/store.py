"""Library persistence: load/save AbstractionLibrary, write runtime module."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from semipy.library.abstractions import (
    AbstractionLibrary,
    ASTPattern,
    LibraryPrimitive,
    PatternOccurrence,
)


LIBRARY_FILENAME = "library.json"
LIBRARY_RUNTIME_FILENAME = "_library.semi.py"


def _primitive_to_dict(p: LibraryPrimitive) -> dict[str, Any]:
    return {
        "primitive_id": p.primitive_id,
        "name": p.name,
        "source": p.source,
        "signature": p.signature,
        "pattern_id": p.pattern_id,
        "occurrence_count": p.occurrence_count,
        "commit_ids": list(p.commit_ids),
        "validated": p.validated,
        "description": p.description,
        "tags": list(p.tags),
        "embedding_id": p.embedding_id or "",
    }


def _primitive_from_dict(d: dict[str, Any]) -> LibraryPrimitive:
    return LibraryPrimitive(
        primitive_id=d["primitive_id"],
        name=d["name"],
        source=d["source"],
        signature=d["signature"],
        pattern_id=d["pattern_id"],
        occurrence_count=int(d.get("occurrence_count", 0)),
        commit_ids=list(d.get("commit_ids", [])),
        validated=bool(d.get("validated", False)),
        description=d.get("description", ""),
        tags=list(d.get("tags", [])),
        embedding_id=d.get("embedding_id", "") or "",
    )


def _pattern_to_dict(p: ASTPattern) -> dict[str, Any]:
    return {
        "pattern_id": p.pattern_id,
        "normalized_source": p.normalized_source,
        "parameter_names": list(p.parameter_names),
        "ast_hash": p.ast_hash,
        "embedding_id": p.embedding_id or "",
    }


def _pattern_from_dict(d: dict[str, Any]) -> ASTPattern:
    return ASTPattern(
        pattern_id=d["pattern_id"],
        normalized_source=d["normalized_source"],
        parameter_names=list(d.get("parameter_names", [])),
        ast_hash=d["ast_hash"],
        embedding_id=d.get("embedding_id", "") or "",
    )


def _occurrence_to_dict(o: PatternOccurrence) -> dict[str, Any]:
    return {
        "session_id": o.session_id,
        "slot_id": o.slot_id,
        "commit_id": o.commit_id,
        "start_line": o.start_line,
        "end_line": o.end_line,
        "binding": dict(o.binding),
    }


def _occurrence_from_dict(d: dict[str, Any]) -> PatternOccurrence:
    return PatternOccurrence(
        session_id=d.get("session_id", ""),
        slot_id=d.get("slot_id", ""),
        commit_id=d.get("commit_id", ""),
        start_line=int(d.get("start_line", 0)),
        end_line=int(d.get("end_line", 0)),
        binding=dict(d.get("binding", {})),
    )


def _library_to_dict(lib: AbstractionLibrary) -> dict[str, Any]:
    return {
        "version": lib.version,
        "last_analyzed_commits": list(lib.last_analyzed_commits),
        "primitives": {k: _primitive_to_dict(v) for k, v in lib.primitives.items()},
        "patterns": {k: _pattern_to_dict(v) for k, v in lib.patterns.items()},
        "occurrences": [_occurrence_to_dict(o) for o in lib.occurrences],
    }


def _library_from_dict(d: dict[str, Any]) -> AbstractionLibrary:
    primitives = {k: _primitive_from_dict(v) for k, v in d.get("primitives", {}).items()}
    patterns = {k: _pattern_from_dict(v) for k, v in d.get("patterns", {}).items()}
    occurrences = [_occurrence_from_dict(o) for o in d.get("occurrences", [])]
    return AbstractionLibrary(
        primitives=primitives,
        patterns=patterns,
        occurrences=occurrences,
        version=int(d.get("version", 0)),
        last_analyzed_commits=set(d.get("last_analyzed_commits", [])),
    )


def load_library(cache_dir: Path) -> AbstractionLibrary:
    """Load AbstractionLibrary from cache_dir/library.json or return empty library."""
    path = cache_dir / LIBRARY_FILENAME
    if not path.exists():
        return AbstractionLibrary()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return _library_from_dict(data)
    except Exception:
        return AbstractionLibrary()


def save_library(cache_dir: Path, library: AbstractionLibrary) -> None:
    """Persist AbstractionLibrary to cache_dir/library.json."""
    path = cache_dir / LIBRARY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_library_to_dict(library), f, indent=2)


def write_library_runtime_module(cache_dir: Path, library: AbstractionLibrary) -> Path:
    """Write an importable Python module with all validated primitives. Returns path to the module."""
    runtime_dir = cache_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / LIBRARY_RUNTIME_FILENAME
    lines = [
        "# Auto-generated library runtime: validated primitives from pattern mining.",
        "from __future__ import annotations",
        "",
    ]
    for prim in library.primitives.values():
        if not prim.validated:
            continue
        lines.append("")
        lines.append(f"# {prim.name}: {prim.signature}")
        if prim.description:
            lines.append(f"# {prim.description}")
        lines.append(prim.source.strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
