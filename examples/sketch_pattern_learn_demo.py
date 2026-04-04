"""
Learn a filter pattern from one #> slot, then match a second slot with different literals.

Run (from repo root, with venv activated)::

  python examples/sketch_pattern_learn_demo.py --fresh --phase all

What to look for in stderr (verbose pipeline):

- **Phase 1** (``filter_alpha``): lines like ``Implementing code`` and ``Generated`` — first-time
  GENERATE. Then ``sketch library updated for pattern from filter_alpha`` after deterministic
  binding extraction (demo heuristic for this file's spec shape).

- **Phase 2** (``filter_beta``): ``Reusing learned pattern with parameter substitution`` — INSTANTIATE
  without a full agent generation pass. The portal commit for this slot should have
  ``"decision": "INSTANTIATE"`` and code using the new column/value (e.g. ``region`` / ``east``).

- **Phase 3** (``filter_gamma``): ``No reusable implementation`` / ``Implementing code`` — spec uses
  a different operator phrase (``is greater than``), so the equals-filter sketch must not match;
  resolution falls through to GENERATE.

Other flags: ``--phase 1|2|3|all``, ``--fresh`` to wipe ``.semiformal-sketch-demo/``.

Requires OPENROUTER_API_KEY for generation. Binding uses ``classify_with_llm`` (OpenAI if set, else
OpenRouter). The demo still works without LLM binding because it builds a sketch from the first
commit using ``_demo_binding_equals_filter`` when the spec matches the demo pattern.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from semipy import configure, semiformal
from semipy.agents.config import get_config
from semipy.history.version_control import most_recent_branch_head
from semipy.library.binding import (
    SpecPhrase,
    build_semantic_binding,
    extract_binding_async,
)
from semipy.library.sketch import build_code_sketch_from_commit, merge_sketch_into_library
from semipy.library.sketch_store import load_sketch_library, save_sketch_library
from semipy.session_anchor import resolve_portal_anchor
from semipy.store import load_portal
from semipy.types import session_id_from_filename, session_module_name_from_filename

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / ".semiformal-sketch-demo"
_SESSION_SOURCE = str((REPO_ROOT / "examples").resolve())

load_dotenv(REPO_ROOT / ".env")

configure(
    cache_dir=str(CACHE_DIR),
    session_source=_SESSION_SOURCE,
    verbose=True,
)


def _portal_handles() -> tuple[Path, str, str, str]:
    cfg = get_config()
    anchor = str(Path(cfg.session_source or __file__).expanduser().resolve())
    cache_dir = Path(cfg.cache_dir)
    session_id = session_id_from_filename(anchor)
    module_name = session_module_name_from_filename(anchor)
    return cache_dir, session_id, anchor, module_name


def _demo_binding_equals_filter(spec_text: str, source: str):
    """
    Deterministic binding for this demo when LLM extraction is unavailable.
    Spec shape: filter rows where "COL" column equals "VAL".
    Finds df[...] and == '...' fragments in generated source (any quote style).
    """
    m = re.search(
        r'filter rows where "([^"]+)" column equals "([^"]+)"',
        spec_text.strip(),
        re.I | re.DOTALL,
    )
    if not m:
        return None
    col_s, val_s = m.group(1), m.group(2)
    col_m = re.search(rf'df\[([\'\"]){re.escape(col_s)}\1\]', source)
    if not col_m:
        return None
    col_ref = col_m.group(0)
    val_m = re.search(rf'==\s*(([\'\"]){re.escape(val_s)}\2)', source)
    if not val_m:
        return None
    val_ref = val_m.group(1)
    phrases: tuple[SpecPhrase, ...] = (
        SpecPhrase("filter rows where", "operation", "df", None, None),
        SpecPhrase(f'"{col_s}"', "param", col_ref, "col", None),
        SpecPhrase("column", "connective", "column", None, None),
        SpecPhrase(
            "equals",
            "operator",
            "==",
            "cmp",
            ("equals", "==", "is equal to"),
        ),
        SpecPhrase(f'"{val_s}"', "param", val_ref, "val", None),
    )
    return build_semantic_binding(spec_text.strip(), phrases)


def _sync_sketch_from_function(qualname: str) -> bool:
    """Build sketch from the head commit of the slot for qualname; return True if stored."""
    cache_dir, session_id, anchor, module_name = _portal_handles()
    portal = load_portal(cache_dir, session_id, anchor, module_name)
    slot = None
    for sl in portal.slots.values():
        snap = sl.slot_spec if isinstance(sl.slot_spec, dict) else {}
        if snap.get("enclosing_function_qualname") == qualname:
            slot = sl
            break
    if slot is None:
        print(f"[sketch demo] no slot found for {qualname}", file=sys.stderr)
        return False
    head = most_recent_branch_head(slot)
    if head is None:
        print(f"[sketch demo] no commit for {qualname}", file=sys.stderr)
        return False
    spec_text = (slot.slot_spec or {}).get("spec_text") or ""
    if not spec_text.strip():
        return False
    src = head.generated_source or ""

    async def _run() -> None:
        binding = _demo_binding_equals_filter(spec_text, src)
        if binding is None:
            binding = await extract_binding_async(spec_text, src)
        if binding is None:
            raise RuntimeError("binding extraction returned None")
        lib = load_sketch_library(cache_dir)
        snap = slot.slot_spec or {}
        cat = str(snap.get("expected_category") or "statement")
        fv = tuple(snap.get("free_variables") or ())
        sketch = build_code_sketch_from_commit(
            binding,
            src,
            head.commit_id,
            cat,
            fv,
        )
        merge_sketch_into_library(lib, sketch, binding)
        save_sketch_library(cache_dir, lib)

    try:
        asyncio.run(_run())
    except Exception as ex:
        print(f"[sketch demo] sync sketch failed: {ex}", file=sys.stderr)
        return False
    print(f"[sketch demo] sketch library updated for pattern from {qualname}", file=sys.stderr)
    return True


@semiformal
def filter_alpha(df):
    #> filter rows where "status" column equals "active"
    out = ...
    return out


@semiformal
def filter_beta(df):
    #> filter rows where "region" column equals "east"
    out = ...
    return out


@semiformal
def filter_gamma(df):
    #> filter rows where "score" column is greater than 10
    out = ...
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Sketch pattern learn / instantiate demo")
    parser.add_argument(
        "--phase",
        choices=("1", "2", "3", "all"),
        default="all",
        help="1=first slot only, 2=second slot, 3=operator change, all=1 then sync then 2 then 3",
    )
    parser.add_argument("--fresh", action="store_true", help="Delete cache dir before phase 1")
    args = parser.parse_args()

    if args.fresh and CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        print(f"[sketch demo] cleared {CACHE_DIR}", file=sys.stderr)

    import pandas as pd

    df = pd.DataFrame(
        {
            "status": ["active", "idle", "active"],
            "region": ["east", "west", "east"],
            "score": [3, 12, 7],
        }
    )

    if args.phase in ("1", "all"):
        print("\n[sketch demo] === Phase 1: filter_alpha (expect GENERATE) ===\n", file=sys.stderr)
        r1 = filter_alpha(df)
        print("[sketch demo] result rows:", len(r1), file=sys.stderr)
        print(r1, file=sys.stderr)
        ok = _sync_sketch_from_function("filter_alpha")
        if not ok:
            print(
                "[sketch demo] hint: set OPENAI_API_KEY for binding extraction; "
                "check logs above.",
                file=sys.stderr,
            )

    if args.phase in ("2", "all"):
        print("\n[sketch demo] === Phase 2: filter_beta (expect INSTANTIATE in logs) ===\n", file=sys.stderr)
        r2 = filter_beta(df)
        print("[sketch demo] result rows:", len(r2), file=sys.stderr)
        print(r2, file=sys.stderr)

    if args.phase in ("3", "all"):
        print(
            "\n[sketch demo] === Phase 3: filter_gamma (expect ADAPT/GENERATE, not INSTANTIATE) ===\n",
            file=sys.stderr,
        )
        r3 = filter_gamma(df)
        print("[sketch demo] result rows:", len(r3), file=sys.stderr)
        print(r3, file=sys.stderr)


if __name__ == "__main__":
    main()
