"""CSV kit example: company data with semantic select, where, assign_semantic, and pls summaries.

  uv run python examples/use_csv_kit.py

Uses company_data.csv (company_name, world_rank, headquarter, market cap, business_sector).
Semantic column names (e.g. "numeric_market_cap") resolve to actual columns; second call reuses cache.
String conditions in where(); assign_semantic() for derived columns. pretty_little_summary for display.
"""

from __future__ import annotations

from pathlib import Path
import sys

_examples = Path(__file__).resolve().parent
if str(_examples) not in sys.path:
    sys.path.insert(0, str(_examples))

from csv_kit import SemiTable, open_table

try:
    import pretty_little_summary as pls
except ImportError:
    pls = None


def main() -> None:
    data_dir = Path(__file__).parent / "data"
    csv_path = data_dir / "company_data.csv"

    print("=== 1. Load company data ===")
    tbl = open_table(csv_path)
    print("Columns:", tbl.columns)
    if pls:
        r = pls.describe(tbl.to_dataframe())
        print(r.content)
    else:
        print(tbl.show(n=5))
    print()

    print("=== 2. Semantic select: numeric_market_cap (first call may generate; second reuses cache) ===")
    cap = tbl.select("numeric_market_cap")
    print("select('numeric_market_cap') -> columns:", cap.columns)
    if pls and not cap.to_dataframe().empty:
        print(pls.describe(cap.to_dataframe()).content)
    else:
        print(cap.show(n=5))
    cap2 = tbl.select("numeric_market_cap")
    print("Again select('numeric_market_cap'): columns:", cap2.columns)
    print()

    print("=== 3. where with string condition ===")
    above = tbl.where("market cap above 1000 billion")
    print("where('market cap above 1000 billion'):", len(above.to_dataframe()), "rows")
    if pls and not above.to_dataframe().empty:
        print(pls.describe(above.to_dataframe()).content)
    else:
        print(above.show(n=5))
    print()

    print("=== 4. assign_semantic: add derived column then summarize with pls ===")
    with_flag = tbl.assign_semantic(over_market_cap="1 when market cap above 1000 billion else 0")
    print("assign_semantic(over_market_cap='1 when market cap above 1000 billion else 0')")
    print("New columns:", with_flag.columns)
    if pls:
        print(pls.describe(with_flag.to_dataframe()).content)
    else:
        print(with_flag.show(n=5))
    print()

    print("=== 5. sort by world_rank (formal) and by semantic order ===")
    by_rank = tbl.sort(by="world_rank", order="asc")
    print("sort(by='world_rank', order='asc') first 3:")
    print(by_rank.select("company_name", "world_rank", "market cap(Billion USD)").show(n=3))
    try:
        by_cap = tbl.select("numeric_market_cap", "company_name").sort(by="company_name", order="by market cap descending")
        print("sort(order='by market cap descending') first 3:")
        print(by_cap.show(n=3))
    except Exception as e:
        print("(sort semantic example:", e, ")")
    print()

    print("=== 6. merge(on=) formal join (if we had a key); pls on result ===")
    sub = tbl.select("company_name", "world_rank", "market cap(Billion USD)")
    if pls:
        print(pls.describe(sub.to_dataframe()).content)
    print("Done.")


if __name__ == "__main__":
    main()
