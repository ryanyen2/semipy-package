# Weather kit: semi for inference, formal for structure

Weather analysis and visualization. **Formal** code handles loading, plotting layout, and data flow. **Semi** handles inference via `semi("natural language ...", expected_type=...)` inside `@semiformal` where the schema or API mapping is underspecified. Named `semi.foo(...)` is not used; the lowering pipeline only recognizes direct `semi(...)` calls.

## Layer

- **`open_dataset(path)`** – Load CSV (tabular) or netCDF (grid). Returns `WeatherDataset`. Formal loader; netCDF coordinate names may use standalone `semi(...)` once when opening the file.
- **`WeatherDataset`** – `.is_grid()` / `.is_table()`, `.variable_names()`, `.get_2d()`, `.table()`, etc. Grid paths use fixed keys `latitude` / `longitude` in `coord_info` after load.
- **`plot_map(ds, variable=None, title=None)`** – Formal: matplotlib + `get_2d()`.
- **`plot_timeseries(ds, variable, **extra)`** – `@semiformal`: formal figure and plot; `semi(...)` picks the date column, tick `Locator`, and optional axis tweaks from `extra`.
- **`latest_append(user_data, city)`** – `@semiformal`: `semi(...)` asks for an HTTP fetch to a public weather API (agent may emit `requests` / `urllib`); formal `pd.concat` appends the row. **`map_fetched_weather_to_row`** (module-level) uses standalone `semi(...)` to align API dicts to table columns.
- **`preprocess_column(ds, column)`** – `@semiformal`: `semi(...)` returns a coerced Series/list-like from a natural-language cleaning spec.

## Agentic pipeline

Fetch and scraping behavior is **not** a separate named tool on `semi`; it is expressed in the **prompt string** passed to `semi(...)` so the same generator tools (`build_and_run_gist`, etc.) apply. Use `uv run python examples/use_weather_kit.py` with `OPENROUTER_API_KEY` (or your configured provider) when generation runs.

## Run

From repo root:

```bash
uv run python examples/use_weather_kit.py
```

Data: grid netCDF (optional) and `seattle-weather.csv`. `latest_append` prompts the model to use a public HTTP weather API (e.g. Open-Meteo-style endpoints).
