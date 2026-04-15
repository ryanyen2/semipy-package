"""
Learn a filter pattern from one #> slot, then match a second slot with different literals.

Run (from repo root, with venv activated)::

  python examples/sketch_pattern_learn_demo.py --fresh --phase all

What to look for in stderr (verbose pipeline):

- **Phase 1** (``filter_alpha``): lines like ``Implementing code`` and ``Generated`` — first-time
  GENERATE. Sketch binding extraction runs inside semipy after the commit (no extra user code).

- **Phase 2** (``filter_beta``): ``Reusing learned pattern with parameter substitution`` — INSTANTIATE
  without a full agent generation pass, if binding extraction produced a matching sketch.

- **Phase 3** (``filter_gamma``): ``No reusable implementation`` / ``Implementing code`` — spec uses
  a different operator phrase (``is greater than``), so the equals-filter sketch must not match;
  resolution falls through to GENERATE or ADAPT.

Other flags: ``--phase 1|2|3|all``, ``--fresh`` to wipe ``examples/.semiformal``.

Generation uses OpenAI when ``OPENAI_API_KEY`` is set, otherwise OpenRouter. Sketch binding extraction
uses the same stack: OpenAI Responses (``openai_model``, default ``gpt-5.4``) first, then OpenRouter
with ``validator_model``. Disable sketch learning with ``configure(sketch_library_learning=False)``.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from semipy import configure, semiformal

_EXAMPLES_ROOT = Path(__file__).resolve().parent
_CACHE = _EXAMPLES_ROOT / ".semiformal"

configure(
    cache_dir=str(_CACHE),
    session_source=str(_EXAMPLES_ROOT),
    verbose=True,
)


@semiformal
def filter_alpha(df):
    #< [Task] filter rows where status equals active
    #> filter rows where "status" column equals "active"
    out = ...
    #< [When] None input returns {'out': None}
    #< [Then] DataFrame-like inputs filter status column to active rows
    #< [Verify] gist: sample DataFrame kept 2 active rows
    return out


@semiformal
def filter_beta(df):
    #> filter rows where "region" column equals "east"
    out = ...
    return out


@semiformal
def filter_gamma(df):
    #< [Task] Filter rows with score greater than 10
    #< [Given] Input is DataFrame-like with columns status, region, score
    #< [Given] Observed score column is int64
    #< [Then] Wrapped result as {'out': out} for statement slot
    #< [Then] Preserved hard constraint verbatim with unreachable out =
    #< [When] df may be None
    #< [Verify] Ran build_and_run_gist on pandas DataFrame sample
    #< [But] Returning filtered DataFrame directly, rejected because validator requires
    #> filter rows where "score" column is greater than 10
    out = ...
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Sketch pattern learn / instantiate demo")
    parser.add_argument(
        "--phase",
        choices=("1", "2", "3", "all"),
        default="all",
        help="Run phase 1 only, 2 only, 3 only, or full sequence",
    )
    parser.add_argument("--fresh", action="store_true", help="Delete examples/.semiformal before phase 1")
    args = parser.parse_args()

    if args.fresh and _CACHE.exists():
        shutil.rmtree(_CACHE)
        print(f"[sketch demo] cleared {_CACHE}", file=sys.stderr)

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
