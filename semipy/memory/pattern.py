"""PatternLibrary — sketch/pattern candidates for INSTANTIATE resolution.

Wraps library/sketch.py and library/binding.py behind a typed interface.
The sketch_library.json file remains the underlying storage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from semipy.types import SlotSpec


class PatternLibrary:
    """Read/write interface to the sketch pattern library."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir

    def find_match(self, slot_spec: "SlotSpec") -> Optional[tuple[Any, dict[str, str]]]:
        """Find a sketch that can be instantiated for this slot_spec.

        Returns (CodeSketch, hole_values) if a match is found, else None.
        """
        try:
            from semipy.library.sketch import find_sketch_match
            from semipy.library.store import load_library
            library = load_library(self._cache_dir)
            return find_sketch_match(library, slot_spec)
        except Exception:
            return None

    def record_binding(
        self,
        spec_text: str,
        generated_source: str,
        commit_id: str,
    ) -> None:
        """Extract and persist a semantic binding from spec_text + generated_source.

        Runs asynchronously in a background thread to avoid blocking the caller.
        This is a fire-and-forget operation; failures are silently discarded.
        """
        import threading

        def _do_record() -> None:
            try:
                import asyncio
                from semipy.library.binding import extract_binding_async
                from semipy.library.sketch import build_code_sketch_from_commit
                from semipy.library.store import load_library, save_library
                from semipy.library.sketch import merge_sketch_into_library

                loop = asyncio.new_event_loop()
                binding = loop.run_until_complete(
                    extract_binding_async(spec_text, generated_source)
                )
                loop.close()
                if binding is None:
                    return
                library = load_library(self._cache_dir)
                sketch = build_code_sketch_from_commit(binding, generated_source, commit_id)
                if sketch is not None:
                    merge_sketch_into_library(library, binding, sketch)
                    save_library(self._cache_dir, library)
            except Exception:
                pass

        threading.Thread(target=_do_record, daemon=True).start()
