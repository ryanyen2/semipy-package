"""
Manual exploratory script for dreamcoder-revision Scenario B:
Same template, different constant (non-loop). First call GENERATEs, second should REUSE.

Run from repo root: uv run python examples/manual_dreamcoder_scenario_b.py
Output should be captured to .claude/manual-test-run.log for inspection.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd
from semipy import semiformal, semi
from semipy.agents.config import configure

# Use a dedicated cache dir for this manual test so we can inspect state cleanly
CACHE = Path(".semiformal_manual_test")
configure(cache_dir=CACHE, verbose=True)

@semiformal("data pipeline")
def analyze():
    # Scenario B: same call site (same line), same template, different constant each iteration.
    df = pd.DataFrame({"year": [2020, 2021, 2022, 2023], "value": [1, 2, 3, 4]})
    for i, threshold in enumerate([2021, 2022], start=1):
        # Single call site: this line runs twice with different threshold.
        filtered = semi(f"remove rows from dataframe where year > {threshold}")
        out = filtered(df)
        print(f"--- Call {i}: threshold={threshold} (expect GENERATE then REUSE) ---")
        print("result shape:", out.shape if hasattr(out, "shape") else type(out))
    print("Done. Second call should show Decision: REUSE and no LLM.")

if __name__ == "__main__":
    analyze()
