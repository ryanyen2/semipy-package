"""Weather kit: formal + semiformal, Herbie-style.
Formal: open_dataset(), WeatherDataset. Semiformal: plot_map(), current_summary().
  uv run --extra example python examples/use_weather_kit.py
"""

from pathlib import Path

# Ensure examples dir is on path for weather_kit
import sys
_examples = Path(__file__).resolve().parent
if str(_examples) not in sys.path:
    sys.path.insert(0, str(_examples))

from weather_kit import open_dataset, plot_map, current_summary, plot_timeseries


def main():
    data_dir = Path(__file__).parent / "data"

    print("=== 1. Formal: load grid data (netCDF) ===")
    nc_path = data_dir / "20190722000000-OSPO-L4_GHRSST-SSTfnd-Geo_Polar_Blended-GLOB-v02.0-fv01.0.nc"
    if not nc_path.exists():
        print("NetCDF not found; skipping grid demo.")
    else:
        ds = open_dataset(nc_path)
        print(ds.description())
        print("Variables:", ds.variable_names())

        print("\n=== 2. Semiformal: plot map (generated code uses ds.get_2d, matplotlib) ===")
        fig = plot_map(ds, variable="analysed_sst", title="Sea surface temperature")
        if fig is not None:
            fig.savefig(Path(__file__).parent / "output/weather_map_sst.png", dpi=120)
            print("Saved weather_map_sst.png")
        else:
            print("plot_map returned None")

    print("\n=== 3. Formal: load table (CSV) ===")
    csv_path = data_dir / "seattle-weather.csv"
    ds_table = open_dataset(csv_path)
    print(ds_table.description())

    print("\n=== 4. Semiformal: time series (generated code plots variable vs date) ===")
    fig_ts = plot_timeseries(ds_table, variable="temp_max", date_column="date")
    if fig_ts is not None:
        fig_ts.savefig(Path(__file__).parent / "output/weather_timeseries.png", dpi=120)
        print("Saved weather_timeseries.png")

    print("\n=== 5. Semiformal: current conditions (fetch-augmented) ===")
    summary = current_summary("Seattle")
    print("Seattle now:", summary)


if __name__ == "__main__":
    main()
