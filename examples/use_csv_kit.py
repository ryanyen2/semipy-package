"""COVID-19 regional burden analysis using the CSV kit.

  uv run python examples/use_csv_kit.py

Loads covid_19_clean_complete.csv. Mixes formal pipeline code (filters, merges)
with @semiformal helpers inside csv_kit and a hybrid CovidReportBuilder: formal
thresholds and loops plus #> blocks and inline semi() for prose and captions
(same spirit as use_visualization_builder: most code formal, open regions where
the user states beliefs or underspecified intent).
"""

from __future__ import annotations

from pathlib import Path
import sys

_examples = Path(__file__).resolve().parent
if str(_examples) not in sys.path:
    sys.path.insert(0, str(_examples))

from csv_kit import CovidReportBuilder, SemiTable, open_table


def main() -> None:
    data_dir = Path(__file__).parent / "data"
    tbl = open_table(data_dir / "covid_19_clean_complete.csv")

    high_burden = tbl.where(Confirmed__gte=50_000).where(
        "country or region in WHO Europe or Americas"
    )
    ts_cols = high_burden.select("date")
    geo_cols = high_burden.select(like="geography or location")

    report_cols = list(ts_cols.columns) + [c for c in geo_cols.columns if c not in ts_cols.columns]
    if not report_cols:
        report_cols = high_burden.columns[:8]
    view = high_burden.select(*report_cols)

    by_date = view.sort(by="Date", order="asc")
    by_severity = by_date.sort(by="Confirmed", order="by worst affected first")

    with_metrics = by_severity.assign_semantic(
        case_fatality_ratio="Deaths / Confirmed if Confirmed > 0 else 0",
        burden_phase="early outbreak if Confirmed < 10000 else peak if Confirmed < 500000 else decline",
    )

    sorted_by_confirmed = with_metrics.sort(by="Confirmed", order="desc")
    top20_df = sorted_by_confirmed.to_dataframe().head(20)
    print("Top 20 rows by confirmed cases (after filters and derived columns):")
    print(SemiTable(top20_df).show(n=20))
    print()

    europe = tbl.where("WHO region is Europe").select(
        "Country/Region", "Date", "Confirmed", "Deaths", "WHO Region"
    )
    americas = tbl.where("WHO region is Americas").select(
        "Country/Region", "Date", "Confirmed", "Deaths", "WHO Region"
    )
    latest_date = (
        tbl.sort(by="Date", order="desc").to_dataframe()["Date"].iloc[0]
        if "Date" in tbl.columns
        else None
    )
    if latest_date is not None:
        eu_snapshot = europe.where(**{"Date": latest_date})
        am_snapshot = americas.where(**{"Date": latest_date})
        print(f"Latest date {latest_date}: Europe snapshot (first 8):")
        print(eu_snapshot.show(n=8))
        print("Americas snapshot (first 8):")
        print(am_snapshot.show(n=8))
    print()

    left_df = (
        tbl.select("Country/Region", "Date", "Confirmed")
        .sort(by="Date", order="asc")
        .to_dataframe()
        .head(2000)
    )
    right_df = (
        tbl.select("Country/Region", "Date", "Deaths", "Recovered")
        .sort(by="Date", order="asc")
        .to_dataframe()
        .head(2000)
    )
    left_t = SemiTable(left_df)
    right_t = SemiTable(right_df)
    merged_formal = left_t.merge(right_t, on=["Country/Region", "Date"], how="inner")
    print("Formal merge on Country/Region, Date (cases + deaths/recovered):")
    print(merged_formal.show(n=8))
    print()

    try:
        combined = left_t.merge_semantic(right_t, how="match by country and date")
        print("Semantic merge (match by country and date):")
        print(combined.show(n=8))
    except Exception:
        print("Semantic merge skipped.")
    print()

    print("Hybrid report builder (#> opening + semi() captions in a loop):")
    builder = CovidReportBuilder(tbl)
    try:
        opening = builder.narrative_opening(confirmed_floor=10_000)
        print("Opening:", opening)
        caps = builder.column_captions(max_cols=5)
        for k, v in caps.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print("CovidReportBuilder skipped:", e)
    print("Done.")


if __name__ == "__main__":
    main()
