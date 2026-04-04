"""Sleep phase orchestrator: collect commits, mine patterns, compress, persist, emit LIBRARY_UPDATED."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from semipy.agents.config import get_config
from semipy.library.abstractions import AbstractionLibrary
from semipy.library.compression import compress_pattern_async
from semipy.library.pattern_mining import mine_patterns
from semipy.library.store import load_library, save_library, write_library_runtime_module
from semipy.library.sketch_store import load_sketch_library, save_sketch_library


def _dedupe_sketch_structural_index(cache_dir: Path) -> None:
    """Normalize structural_index lists (unique sketch ids per signature)."""
    lib = load_sketch_library(cache_dir)
    si: dict[str, list[str]] = {}
    for sig, ids in lib.structural_index.items():
        seen: set[str] = set()
        out: list[str] = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        si[sig] = out
    lib.structural_index = si
    lib.version += 1
    save_sketch_library(cache_dir, lib)


def _collect_commits_from_portals(cache_dir: Path) -> list[tuple[str, str, str, str]]:
    """Collect (session_id, slot_id, commit_id, generated_source) from all portal files in cache_dir."""
    import json
    results: list[tuple[str, str, str, str]] = []
    for path in cache_dir.glob("*.portal.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        session_id = data.get("session_id", "")
        for slot_id, slot_data in data.get("slots", {}).items():
            for cid, c in slot_data.get("commits", {}).items():
                src = c.get("generated_source", "")
                if src and src.strip():
                    results.append((session_id, slot_id, cid, src))
    return results


async def run_sleep_phase_async(
    cache_dir: Optional[Path] = None,
    min_new_commits: int = 3,
    emit_event: Optional[Any] = None,
    skip_llm: bool = False,
    skip_gist: bool = True,
) -> AbstractionLibrary:
    """
    Run the sleep phase: mine patterns from all commits, compress into primitives, persist library.
    If the number of new (not yet analyzed) commits is < min_new_commits, return existing library without mining.
    emit_event: optional callable(ReactiveEvent) to emit LIBRARY_UPDATED.
    skip_llm / skip_gist: for tests or when LLM/sandbox unavailable.
    """
    config = get_config()
    cache_dir = cache_dir or Path(config.cache_dir)
    library = load_library(cache_dir)
    commits_raw = _collect_commits_from_portals(cache_dir)
    already = library.last_analyzed_commits
    new_commits = [(sid, slot_id, cid, src) for sid, slot_id, cid, src in commits_raw if cid not in already]
    if len(new_commits) < min_new_commits:
        return library
    commit_sources = [(cid, src) for _s, _slot, cid, src in new_commits]
    min_freq = 3
    pattern_groups = mine_patterns(
        commit_sources,
        min_pattern_frequency=min_freq,
        min_nodes=5,
        max_nodes=200,
    )
    for pattern, group in pattern_groups:
        library.patterns[pattern.pattern_id] = pattern
    new_primitives: list[Any] = []
    for pattern, group in pattern_groups:
        prim = await compress_pattern_async(
            pattern,
            group,
            library,
            skip_llm=skip_llm,
            skip_gist=skip_gist,
        )
        if prim is not None:
            library.primitives[prim.primitive_id] = prim
            new_primitives.append(prim)
    library.last_analyzed_commits = already | {cid for _s, _slot, cid, _src in new_commits}
    library.version += 1
    save_library(cache_dir, library)
    write_library_runtime_module(cache_dir, library)
    try:
        _dedupe_sketch_structural_index(cache_dir)
    except Exception:
        pass
    if emit_event is not None:
        try:
            from semipy.reactivity.events import EventType, ReactiveEvent
            from semipy.reactivity.reactive import SlotRef
            ev = ReactiveEvent(
                event_type=EventType.LIBRARY_UPDATED,
                source_ref=SlotRef(session_id="", slot_id="library"),
                timestamp=__import__("time").time(),
                payload={"version": library.version, "new_primitives": len(new_primitives)},
            )
            emit_event(ev)
        except Exception:
            pass
    return library


def run_sleep_phase(
    cache_dir: Optional[Path] = None,
    min_new_commits: int = 3,
    emit_event: Optional[Any] = None,
    skip_llm: bool = False,
    skip_gist: bool = True,
) -> AbstractionLibrary:
    """Synchronous wrapper for run_sleep_phase_async."""
    return asyncio.run(
        run_sleep_phase_async(
            cache_dir=cache_dir,
            min_new_commits=min_new_commits,
            emit_event=emit_event,
            skip_llm=skip_llm,
            skip_gist=skip_gist,
        )
    )
