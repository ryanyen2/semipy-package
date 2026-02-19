"""Weather kit: formal first, semiformal only where schema/data are unknown.

  uv run --extra example python examples/use_weather_kit.py
"""

from pathlib import Path
import sys

_examples = Path(__file__).resolve().parent
if str(_examples) not in sys.path:
    sys.path.insert(0, str(_examples))

from weather_kit import (
    open_dataset,
    plot_map,
    plot_timeseries,
    latest_append,
)


def main():
    data_dir = Path(__file__).parent / "data"
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)

    # --- Formal: load and plot (no semi) ---
    # print("=== 1. Formal: load grid (netCDF) ===")
    # nc_path = data_dir / "20190722000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended-GLOB-v02.0-fv01.0.nc"
    # if nc_path.exists():
    #     ds_grid = open_dataset(nc_path)
    #     print(ds_grid.description())
    #     print("Variables:", ds_grid.variable_names())

    #     print("\n=== 2. Formal: plot map (deterministic) ===")
    #     fig = plot_map(ds_grid, variable="analysed_sst", title="Sea surface temperature")
    #     if fig is not None:
    #         fig.savefig(out_dir / "weather_map_sst.png", dpi=120)
    #         print("Saved output/weather_map_sst.png")

    # --- Formal: load table; plot with explicit or inferred date column ---
    print("\n=== 3. Formal: load table (CSV) ===")
    csv_path = data_dir / "seattle-weather.csv"
    ds_table = open_dataset(csv_path)
    print(ds_table.description())

    print("\n=== 4. Formal: plot time series (explicit date_column) ===")
    fig_ts = plot_timeseries(
        ds_table, 
        variable="temp_max"
    )
    fig_ts.savefig(out_dir / "weather_timeseries.png", dpi=120)
    print("Saved output/weather_timeseries.png")

    # --- Semiformal only when needed: infer date column if we didn't know it ---
    # print("\n=== 5. Semiformal: infer date column (when schema unknown) ===")
    # inferred = infer_date_column(ds_table)
    # print("Inferred date column:", inferred)

    # --- latest_append: formal fetch + append (semi only if schema unmapped) ---
    # print("\n=== 6. latest_append: fetch latest and append to existing data ===")
    # try:
    #     ds_with_latest = latest_append(ds_table, "Seattle")
    #     tbl = ds_with_latest.table()
    #     print("Rows before:", len(ds_table.table()), "-> after:", len(tbl))
    #     print("Last row (appended):", tbl.iloc[-1].to_dict())
    # except Exception as e:
    #     print("latest_append error:", e)


if __name__ == "__main__":
    main()
