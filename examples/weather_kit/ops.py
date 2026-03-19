"""Weather kit ops: @semiformal where inference is needed; formal structure elsewhere.

Uses semi("...", expected_type=...) only (named semi.* was removed from the runtime).
Fetch and HTTP-style tasks are expressed as natural-language prompts so the agentic
pipeline can emit requests/urllib code when appropriate.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from semipy import semiformal, semi

from weather_kit.dataset import WeatherDataset

import matplotlib.pyplot as plt
import numpy as np


def plot_map(
    ds: WeatherDataset,
    variable: Optional[str] = None,
    title: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Plot a 2D map of a grid variable (formal layout)."""
    _ = kwargs
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


@semiformal
def plot_timeseries(
    ds: WeatherDataset,
    variable: str,
    **extra: Any,
) -> Any:
    """Formal figure setup; semi for date column and tick formatting when schema is unknown."""
    tbl = ds.table()
    if len(tbl.columns) == 0:
        plt.figure()
        return plt.gcf()

    date_column = semi(
        f"Which column in {list(tbl.columns)!r} is the date or time axis for a time series?",
        expected_type=str,
    )
    if date_column not in tbl.columns:
        date_column = tbl.columns[0]

    plt.figure()
    plt.plot(tbl[date_column], tbl[variable])
    plt.xlabel(date_column)
    loc = semi(
        f"matplotlib.ticker Locator suitable for axis with pandas datetime-like column {date_column!r}.",
        expected_type=plt.Locator,
    )
    plt.gca().xaxis.set_major_locator(loc)
    plt.ylabel(variable)
    if extra:
        semi(
            f"Apply matplotlib axis tweaks from parameters {extra!r} for x={date_column!r} y={variable!r}; "
            f"use plt.gca() only. Return None.",
            expected_type=type(None),
        )
    return plt.gcf()


def map_fetched_weather_to_row(fetched_weather: dict[str, Any], table: pd.DataFrame) -> dict[str, Any]:
    """Standalone semi: align API payload to table columns (not inside @semiformal)."""
    columns = table.columns.tolist()
    date_guess = semi(
        f"Best date or time column name among {columns!r} for aligning a weather API row.",
        expected_type=str,
    )
    if not date_guess or date_guess not in columns:
        date_guess = columns[0] if columns else None
    row = semi(
        f"Map weather API payload {fetched_weather!r} to one table row with keys exactly {columns!r}. "
        f"Prefer date column {date_guess!r} for timestamps. Return dict.",
        expected_type=dict,
    )
    if not isinstance(row, dict):
        row = {}
    return {c: row.get(c, pd.NA) for c in columns}


@semiformal
def latest_append(user_data: WeatherDataset, city: str, **extra: Any) -> WeatherDataset:
    """Fetch latest weather via HTTP (prompt instructs agent); formal concat."""
    if not user_data.is_table():
        raise ValueError("latest_append requires a table dataset")

    latest = semi(
        f"Fetch current weather for location {city!r} using a public HTTP weather API "
        f"(requests or urllib). Return a JSON-serializable dict with fields that can map to a daily table "
        f"(temperature, conditions, precipitation, datetime string, etc.). Extra hints: {extra!r}.",
        expected_type=dict,
    )
    if isinstance(latest, dict) and latest.get("error"):
        raise ValueError(str(latest["error"]))
    table = user_data.table()
    row = map_fetched_weather_to_row(latest, table)
    if not isinstance(row, dict):
        row = {}
    row = {c: row.get(c, pd.NA) for c in table.columns}
    new_row_df = pd.DataFrame([row])
    combined = pd.concat([table, new_row_df], ignore_index=True)
    return WeatherDataset.from_table(combined, source_path=user_data.source_path)


@semiformal
def preprocess_column(ds: WeatherDataset, column: str, **extra: Any) -> WeatherDataset:
    """Coerce or clean one column; semantics from data and prompt."""
    if not ds.is_table():
        raise ValueError("preprocess_column requires a table dataset")
    tbl = ds.table()
    if column not in tbl.columns:
        raise ValueError(f"Column {column!r} not in dataset")
    series = tbl[column]
    modified = semi(
        f"Return a pandas Series or list-like of length {len(series)} replacing column {column!r}: "
        f"coerce types, parse dates if needed, handle missing values. Sample dtype: {getattr(series, 'dtype', type(series))}. "
        f"Extra: {extra!r}.",
        expected_type=Any,
    )
    if modified is None or not hasattr(modified, "__len__"):
        return ds
    if len(modified) != len(tbl):
        return ds
    new_tbl = tbl.assign(**{column: modified})
    return WeatherDataset.from_table(new_tbl, source_path=ds.source_path)
