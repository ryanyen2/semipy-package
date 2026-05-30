"""User-style demo: a semiformal date formatter under format drift.

Exercises the behavioral-contract subsystem end to end against the real LLM:
GENERATE -> REUSE -> ADAPT, with the acceptance gate and the LLM maintainer on,
so we can inspect the synthesized code, the contract cases, the change records,
and the #< surfaces.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from semipy import configure, semiformal

_ROOT = Path(__file__).resolve().parent
CACHE = str(_ROOT / ".contract_demo_cache")

configure(
    cache_dir=CACHE,
    session_source=str(Path(__file__).resolve()),
    openai_model="gpt-5.5",          # latest OpenAI model (not the legacy gpt-5.4 default)
    verbose=True,
    contract_enabled=True,
    contract_gate=True,              # enforce carried-forward decisions
    contract_block_regressions=True,
    contract_maintainer=True,        # run the LLM TDD/BDD maintainer pass
    contract_maintainer_async=False,
)


@semiformal
def to_month_year(date_str: str) -> str:
    #< intent: Format inferred date as abbreviated month and year
    #< by: probing common date patterns, then email-date parsing
    #< unless: empty or unparseable input yields empty formatted value
    #> infer the input date format from the value and return it formatted as "%b %Y"
    #> (abbreviated month and 4-digit year, e.g. "Mar 2025")
    formatted = ...
    return formatted


def run() -> None:
    print("\n===== BATCH 1: slash dates (first call -> GENERATE, rest REUSE) =====")
    batch1 = ["03/14/2025", "03/20/2025", "04/05/2025", "04/18/2025"]
    for raw in batch1:
        print(f"  {raw!r:>18} -> {to_month_year(raw)!r}")

    print("\n===== BATCH 2: dotted format (likely unhandled -> empty -> ADAPT) =====")
    batch2 = ["2025.06.21", "2026.07.09", "2025.08.18"]
    for raw in batch2:
        print(f"  {raw!r:>18} -> {to_month_year(raw)!r}")

    print("\n===== BATCH 3: original slash dates again (must still work, no regression) =====")
    batch3 = ["05/01/2025", "06/18/2025"]
    for raw in batch3:
        print(f"  {raw!r:>18} -> {to_month_year(raw)!r}")


if __name__ == "__main__":
    run()
