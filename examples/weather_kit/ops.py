"""Weather kit ops: semiformal where inference is needed.

Uses semi() and semi.<name>() for: picking date/time columns, mapping fetched
weather to table rows, weather code labels, and column preprocessing. No
hardcoded patterns or long conditionals; the program infers from data and context.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from semipy import semiformal, semi

from weather_kit.dataset import WeatherDataset

import matplotlib.pyplot as plt
import numpy as np


# --- Plotting: formal structure, semi for underspecified choices ---

def plot_map(
    ds: WeatherDataset,
    variable: Optional[str] = None,
    title: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Plot a 2D map of a grid variable."""
    ds_plot = ds.subset_for_plot()
    v = variable or (ds.variable_names()[0] if ds.variable_names() else None)
    lon, lat, values = ds_plot.get_2d(v)
    lon, lat, values = np.asarray(lon), np.asarray(lat), np.asarray(values)
    if lon.ndim == 1 and lat.ndim == 1:
        lon, lat = np.meshgrid(lon, lat)
    
    plt.figure()
    plt.pcolormesh(lon, lat, values, shading="auto")
    plt.colorbar()
    plt.title(title or "")
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    return plt.gcf()


@semiformal("plot a table variable vs time; infer date column when not given")
def plot_timeseries(
    ds: WeatherDataset,
    variable: str,
) -> Any:
    """Plot variable vs time. date_column inferred via semi when None."""
    
    tbl = ds.table()
    date_columns = [col for col in tbl.columns if semi(f"field as datetime from {tbl.columns.tolist()}") in col.lower()]
    date_column = date_columns[0] if date_columns else tbl.columns[0]

    plt.figure()
    plt.plot(tbl[date_column], tbl[variable])
    plt.xlabel(date_column)
    # TODO: this example showng the error of incompatibility with the matplotlib type hints: TypeError: 'locator' must be an instance of matplotlib.ticker.Locator, not a tuple
    # plt.gca().xaxis.set_major_locator(semi(f"set major locator for {date_column} based on the data"))
    plt.gca().xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    plt.gca().xaxis.set_major_formatter(plt.FormatStrFormatter(semi(f"format major ticks for {date_column} based on the data")))
    plt.ylabel(variable)
    plt.gca().tick_params(axis="x", rotation=45)
    return plt.gcf()

def map_fetched_weather_to_row(fetched_weather: dict[str, Any], table: pd.DataFrame) -> dict[str, Any]:
    # 1. find the date column
    # 2. map the fetched weather to the table columns
    
    # 3. return the new row
    date_column = semi.pick_date_column(table.columns.tolist())
    new_row = {date_column: fetched_weather.get("time"), **fetched_weather}
    return new_row

@semiformal("fetch latest weather and append one row to the table")
def latest_append(user_data: WeatherDataset, city: str, **kwargs: Any) -> WeatherDataset:
    """Fetch latest weather for city and append one row. Semi maps fetched data to table columns."""
    if not user_data.is_table():
        raise ValueError("latest_append requires a table dataset")

    latest = semi.fetch_weather(city)
    if isinstance(latest, dict) and latest.get("error"):
        raise ValueError(latest["error"])
    table = user_data.table()
    row = semi.map_fetched_weather_to_row(latest, table.columns.tolist())
    if not isinstance(row, dict):
        row = {}
    row = {c: row.get(c, pd.NA) for c in table.columns}
    new_row_df = pd.DataFrame([row])
    combined = pd.concat([table, new_row_df], ignore_index=True)
    return WeatherDataset.from_table(combined, source_path=user_data.source_path)


@semiformal("preprocess a column: coerce type, parse dates, handle missing")
def preprocess_column(ds: WeatherDataset, column: str, **kwargs: Any) -> WeatherDataset:
    """Preprocess a column using semi; type and semantics inferred from data."""
    if not ds.is_table():
        raise ValueError("preprocess_column requires a table dataset")
    tbl = ds.table()
    if column not in tbl.columns:
        raise ValueError(f"Column {column!r} not in dataset")
    series = tbl[column]
    modified = semi.preprocess_series(series, column)
    if modified is None or not hasattr(modified, "__len__"):
        return ds
    if len(modified) != len(tbl):
        return ds
    new_tbl = tbl.assign(**{column: modified})
    return WeatherDataset.from_table(new_tbl, source_path=ds.source_path)
