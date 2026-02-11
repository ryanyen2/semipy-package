# Weather kit: formal + semiformal

A small weather analysis and visualization library that mixes **formal** (deterministic) and **semiformal** (intent-driven, generated) code. Inspired by [Herbie](https://herbie.readthedocs.io/) (e.g. [model terrain](https://herbie.readthedocs.io/en/stable/gallery/bonus_notebooks/model_terrain.html)): load data, then say what you want; the system generates the code that does it.

## Formal layer

- **`open_dataset(path)`** – Load CSV (tabular) or netCDF (grid). Returns a `WeatherDataset`.
- **`WeatherDataset`** – Unified handle:
  - `.is_grid()` / `.is_table()` – Data kind.
  - `.variable_names()` – Plottable variables.
  - `.get_2d(variable)` – For grids: `(lon, lat, values)` for map plotting.
  - `.subset_for_plot(max_points)` – Subsample large grids for fast plotting.
  - `.table()` – For tables: pandas DataFrame.
  - `.description()` – Short summary for prompts.

All of this is typed, deterministic, and testable.

## Semiformal layer

- **`plot_map(ds, variable=None, title=None)`** – “Plot this dataset as a 2D map.” Generated code uses `ds.get_2d()`, `subset_for_plot()`, matplotlib (pcolormesh, colorbar). Returns figure or None.
- **`plot_timeseries(ds, variable, date_column=None)`** – “Plot this variable vs time.” For tabular data; generated code uses `ds.table()` and matplotlib.
- **`current_summary(city)`** – “Summarize current weather.” Uses `FETCH_WEATHER(city)` (Open-Meteo); the program is incomplete without the fetch.
- **`ask(city, question)`** – “Answer a question about current weather.” Fetch-augmented; returns str or bool.

Generated implementations are cached; later calls reuse them.

## Run

From repo root, with optional deps (xarray, netcdf4, matplotlib):

```bash
uv run --extra example python examples/use_weather_kit.py
```

Or install manually: `pip install xarray netcdf4 matplotlib`, then run the script (with `examples` on `PYTHONPATH` or from `examples/`).

## Data

- **Grid**: `examples/data/20190722000000-OSPO-L4_GHRSST-*.nc` (sea surface temperature).
- **Table**: `examples/data/seattle-weather.csv`.

No API keys for Open-Meteo (current weather).
