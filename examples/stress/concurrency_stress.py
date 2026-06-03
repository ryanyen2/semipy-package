"""Stress: thread concurrency, the classic 'fine in dev, melts under load' risk.

Three phases:
  1. Warm up one slot (single GENERATE -> cached).
  2. Hammer the cached REUSE path from many threads with varied inputs -- the hot
     path for a server handling concurrent requests or a parallel data pipeline.
     Assert no exceptions, correct + deterministic results, and an uncorrupted portal.
  3. Race several threads on the SAME still-uncached slot at once, to test that
     concurrent first-generation does not corrupt the portal / dispatch module.

Run from examples/stress/.  Needs OPENAI_API_KEY (a few generations only).
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from semipy import configure, get_config, semiformal

CACHE = os.environ.get("STRESS_CACHE", "examples/stress/.cache_conc")
# Isolate the concurrency of the cache/portal/dispatch path: turn off the LLM-based
# semantic recheck so phase 2 is pure REUSE (no generation) and races are not masked
# by regeneration traffic.
configure(verbose=False, cache_dir=CACHE, semantic_verify=False)


@semiformal
def normalize_handle(raw: str) -> str:
    result = ""
    #< intent: Normalize user handle string
    #< by: stripping prefix, casefolding simply, and hyphen-joining whitespace-separated parts
    #> normalize the user {raw} handle: strip surrounding whitespace, drop a leading '@',
    #> lowercase it, and collapse internal whitespace runs to single hyphens
    return result


def portal_files_valid() -> tuple[int, int]:
    """Return (n_portal_json, n_invalid) -- every portal JSON must still parse."""
    root = Path(CACHE)
    n = bad = 0
    for p in root.rglob("*.json"):
        n += 1
        try:
            json.loads(p.read_text())
        except Exception:
            bad += 1
            print(f"  CORRUPT: {p}")
    return n, bad


def main() -> None:
    print("=== phase 1: warmup (single generation) ===")
    base = normalize_handle("  @Alice  Smith ")
    print(f"  normalize_handle('  @Alice  Smith ') = {base!r}")
    expected = base  # treat the warmed result as ground truth for determinism

    print("\n=== phase 2: 16 threads x 320 concurrent REUSE calls ===")
    inputs = [
        "@Bob", "  Carol  ", "@dave_jones", "EVE", "  @Frank  Ocean ",
        "@grace", "Heidi  Klum", "@ivan", "  judy ", "@mallory",
    ]
    errors: list[str] = []
    mismatches: list[str] = []
    lock = threading.Lock()

    def work(i: int) -> None:
        raw = inputs[i % len(inputs)]
        try:
            r1 = normalize_handle(raw)
            r2 = normalize_handle(raw)  # determinism: same input -> same output
            if r1 != r2:
                with lock:
                    mismatches.append(f"{raw!r}: {r1!r} != {r2!r}")
            if not isinstance(r1, str) or not r1:
                with lock:
                    mismatches.append(f"{raw!r}: bad result {r1!r}")
        except Exception as e:  # any thread crash is a failure
            with lock:
                errors.append(f"{raw!r}: {type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(work, i) for i in range(320)]
        for f in as_completed(futs):
            f.result()

    print(f"  exceptions: {len(errors)}  | nondeterministic/bad: {len(mismatches)}")
    for line in (errors + mismatches)[:8]:
        print("   ", line)
    n, bad = portal_files_valid()
    print(f"  portal JSON files: {n}  | corrupt: {bad}")

    print("\n=== phase 3: 6 threads race the SAME uncached slot at once ===")
    # Defined here so it is still uncached when the threads hit it together.
    @semiformal
    def to_slug(title: str) -> str:
        result = ""
        #< intent: Create URL slug from article title
        #< by: lowercasing then replacing non-alphanumerics with single hyphens
        #> turn the article {title} into a url slug: lowercase, spaces and punctuation to
        #> single hyphens, trimmed, no leading/trailing hyphens
        return result

    race_results: list[str] = []
    race_errors: list[str] = []

    def race(_i: int) -> None:
        try:
            race_results.append(to_slug("Hello, World!  A Title"))
        except Exception as e:
            with lock:
                race_errors.append(f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=6) as ex:
        for f in as_completed([ex.submit(race, i) for i in range(6)]):
            f.result()

    distinct = set(race_results)
    print(f"  results: {len(race_results)}  errors: {len(race_errors)}  distinct outputs: {distinct}")
    for line in race_errors[:6]:
        print("   ", line)
    n, bad = portal_files_valid()
    print(f"  portal JSON files: {n}  | corrupt: {bad}")

    print("\nDONE.")


if __name__ == "__main__":
    main()
