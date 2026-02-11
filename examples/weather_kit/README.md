# Weather kit: semi for inference, formal for structure

Weather analysis and visualization. **Formal** code handles loading, plotting structure, and data flow. **Semi** handles inference: which column is date/time, how to map fetched weather to table columns, how to preprocess a column. No hardcoded patterns or long conditionals; behavior is inferred from data and context at runtime.

## Layer

- **`open_dataset(path)`** – Load CSV (tabular) or netCDF (grid). Returns `WeatherDataset`. Formal.
- **`WeatherDataset`** – `.is_grid()` / `.is_table()`, `.variable_names()`, `.get_2d()`, `.table()`, etc. Formal.
- **`plot_map(ds, variable=None, title=None)`** – Formal: matplotlib + `get_2d()`.
- **`plot_timeseries(ds, variable, date_column=None)`** – When `date_column` is None, uses `semi.pick_date_column(columns)` to infer it.
- **`latest_append(user_data, city)`** – Uses `semi.fetch_weather(city)` and `semi.map_fetched_weather_to_row(latest, columns)` to append one row; no manual mapping or weathercode tables.
- **`infer_date_column(ds)`** – Uses `semi.pick_date_column(columns)`.
- **`preprocess_column(ds, column)`** – Uses `semi.preprocess_series(series, column)`; type and semantics inferred from data.

Semi calls used: `semi.pick_date_column`, `semi.fetch_weather`, `semi.map_fetched_weather_to_row`, `semi.preprocess_series`. The program stays readable; inference is delegated to semi.

## Run

From repo root:

```bash
uv run --extra example python examples/use_weather_kit.py
```

Data: grid netCDF and `seattle-weather.csv`. Open-Meteo is used for fetch (no API key).