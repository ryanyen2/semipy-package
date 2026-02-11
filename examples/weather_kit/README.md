# Weather kit: formal first, semiformal only when necessary

Weather analysis and visualization with a clear split: **formal** (deterministic, typed) code does the work; **semiformal** is used only where we cannot predefine behavior (unknown schema, unknown data type, or schema-agnostic merge).

## Formal layer

- **`open_dataset(path)`** – Load CSV (tabular) or netCDF (grid). Returns `WeatherDataset`.
- **`WeatherDataset`** – `.is_grid()` / `.is_table()`, `.variable_names()`, `.get_2d()`, `.subset_for_plot()`, `.table()`, `.description()`, `.from_table(df)`.
- **`plot_map(ds, variable=None, title=None)`** – Deterministic: matplotlib + `get_2d()` / `subset_for_plot()`. No semi.
- **`plot_timeseries(ds, variable, date_column=None)`** – Formal when `date_column` is given; if `None`, uses semi only to infer the date column.
- **`latest_append(user_data, city)`** – Get the **latest** weather for `city` (FETCH_WEATHER) and **append** one row to the user’s existing table. Formal fetch + formal column mapping; semi only when the table has columns we cannot map from the fetch.

## Semiformal only for generalizability

- **`infer_date_column(ds)`** – When the date/time column is unspecified; column names and types are unknown a priori.
- **`_append_latest_row_semi(...)`** – Used inside `latest_append` only when some table columns cannot be filled from the formal mapping.
- **`preprocess_column(ds, column)`** – Preprocess a column without knowing its data type or semantics.

Semi is used only where it solves the “unknown schema / unknown type” problem that would otherwise require hardcoded patterns.

## Run

From repo root:

```bash
uv run --extra example python examples/use_weather_kit.py
```

Data: grid netCDF and `seattle-weather.csv`. No API keys for Open-Meteo.
