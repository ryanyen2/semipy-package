"""CSV kit: table API that looks deterministic. Semi is an implementation detail.

  uv run python examples/use_csv_kit.py

User writes code like spec: column names, by=, order=, keyword predicates.
Semantic options (select(like=...), where_semantic(), sort_semantic(), merge_semantic())
use semi only where the library cannot be fully deterministic.
"""

from __future__ import annotations

from pathlib import Path
import sys

_examples = Path(__file__).resolve().parent
if str(_examples) not in sys.path:
    sys.path.insert(0, str(_examples))

from csv_kit import SemiTable, open_table


def main() -> None:
    data_dir = Path(__file__).parent / "data"
    csv_path = data_dir / "company_data.csv"

    print("=== 1. Load ===")
    tbl = open_table(csv_path)
    print("Columns:", tbl.columns)
    print()

    print("=== 2. Formal: select, sort, where (no semi) ===")
    out = (
        tbl.select("date")
        .sort_semantic(meaning="by market cap")
        .sort(by="market_cap", order="desc")
        .where(market_cap__gt=1000000000000)
    )
    print(out.show(n=6))
    print()

    print("=== 3. select(like=...) - semi only here (resolve columns by meaning) ===")
    numeric = tbl.select(like="numeric")
    print("select(like='numeric'):", numeric.columns)
    print(numeric.show(n=3))
    print()

    print("=== 4. sort(by=, order=) - always formal ===")
    by_date = tbl.sort(by="date", order="asc")
    by_price = tbl.sort(by="price", order="desc")
    print("sort(by='price', order='desc') first 3:")
    print(by_price.select("product_name", "price").show(n=3))
    print()

    print("=== 5. where(**kwargs) - formal predicates ===")
    high = tbl.where(price__gte=50)
    north = tbl.where(region="North")
    print("where(price__gte=50):", len(high.to_dataframe()), "rows")
    print("where(region='North'):", north.show(n=4))
    print()

    print("=== 6. where_semantic() - semi only when predicate is not column-op-value ===")
    try:
        subset = tbl.select("price", "quantity").where_semantic("rows that look like outliers")
        print("where_semantic('...outliers'):", len(subset.to_dataframe()), "rows")
        print(subset.show(n=5))
    except Exception as e:
        print("(where_semantic example:", e, ")")
    print()

    print("=== 7. sort_semantic() - semi only when order is not column asc/desc ===")
    try:
        by_importance = tbl.select("product_name", "price", "quantity").sort_semantic(meaning="by revenue importance")
        print("sort_semantic(meaning='by revenue importance') first 3:")
        print(by_importance.show(n=3))
    except Exception as e:
        print("(sort_semantic example:", e, ")")
    print()

    print("=== 8. merge(on=) - formal join ===")
    extra = tbl.select("product_id", "product_name").where(product_id="P-101")
    merged = tbl.select("date", "product_id", "price").merge(extra, on="product_id")
    print("merge(on='product_id') first 3:")
    print(merged.show(n=3))
    print()

    print("=== 9. merge_semantic() - semi only for row matching by meaning ===")
    try:
        other = tbl.select("product_name", "region").where(region="North")
        combined = tbl.select("product_name", "price").merge_semantic(other, how="match rows by product name")
        print("merge_semantic(how='match rows by product name'):", len(combined.to_dataframe()), "rows")
        print(combined.show(n=3))
    except Exception as e:
        print("(merge_semantic example:", e, ")")
    print("Done.")


if __name__ == "__main__":
    main()
