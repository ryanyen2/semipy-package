"""Weather kit ops: mostly formal, semiformal only where schema/data are unknown.

Formal: plot_map, plot_timeseries (explicit params), fetch latest.
Semiformal: infer_date_column (when date column is unspecified), append_latest_row
(merge fetched record into arbitrary table schema), preprocess_column (when data type unknown).
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from semipy import semiformal, semi

from weather_kit.dataset import WeatherDataset


# --- Formal: plotting (contract is known) ---

def plot_map(
    ds: WeatherDataset,
    variable: Optional[str] = None,
    title: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Plot a 2D map of a grid variable. Formal: deterministic matplotlib + get_2d."""
    if not ds.is_grid():
        raise ValueError("plot_map requires a grid dataset")
    import matplotlib.pyplot as plt
    import numpy as np
    ds_plot = ds.subset_for_plot()
    v = variable or (ds.variable_names()[0] if ds.variable_names() else None)
    lon, lat, values = ds_plot.get_2d(v)
    lon, lat, values = np.asarray(lon), np.asarray(lat), np.asarray(values)
    if lon.ndim == 1 and lat.ndim == 1:
        lon, lat = np.meshgrid(lon, lat)
    plt.figure()
    plt.pcolormesh(lon, lat, values, shading="auto")
    plt.colorbar()
    if title:
        plt.title(title)
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    return plt.gcf()


def plot_timeseries(
    ds: WeatherDataset,
    variable: str,
    date_column: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Plot variable vs time for a table. Formal when date_column is given; else semi infers it."""
    if not ds.is_table():
        raise ValueError("plot_timeseries requires a table dataset")
    tbl = ds.table()
    if variable not in tbl.columns:
        raise ValueError(f"Column {variable!r} not in {list(tbl.columns)}")
    if date_column is None:
        # date_column = infer_date_column(ds)
        date_column = semi(f"field like date or time from {ds.table().columns.tolist()}")
        date_columns = [col for col in tbl.columns if semi(f"date or time") in col.lower()]
        if len(date_columns) == 1:
            date_column = date_columns[0]
        else:
            raise ValueError(f"Multiple date columns found: {date_columns}")

    if date_column is None or date_column not in tbl.columns:
        raise ValueError("No date column found or specified")
    
    import matplotlib.pyplot as plt
    import pandas as pd
    df = tbl.copy()
    df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
    df = df.dropna(subset=[date_column, variable])
    plt.figure()
    plt.plot(df[date_column], df[variable])
    plt.xlabel(date_column)
    plt.ylabel(variable)
    plt.gca().tick_params(axis="x", rotation=45)
    plt.tight_layout()
    return plt.gcf()


# --- Semiformal: only when we cannot predefine (schema / type unknown) ---

@semiformal("infer which column in the table is the date/time column")
def infer_date_column(ds: WeatherDataset, **kwargs: Any) -> Optional[str]:
    """Infer the date or time column name. Semiformal: column names and types are unknown a priori."""
    if not ds.is_table():
        return None
    cols = ds.table().columns.tolist()
    return semi(
        f"""Given table columns: {cols}. Return the single column name that best represents date or time.
        Return only the column name as a string, or None if none clearly fit. No explanation."""
    )


def latest_append(user_data: WeatherDataset, city: str, **kwargs: Any) -> WeatherDataset:
    """Fetch latest weather for city and append one row to user_data. Formal fetch + semi only when schema is unknown.

    Formal: call FETCH_WEATHER(city) to get the latest dataset; map to common weather columns and append.
    Semiformal: when the table has columns we cannot map from the fetch, use generated code to merge.
    """
    if not user_data.is_table():
        raise ValueError("latest_append requires a table dataset")

    weather_format = {
        "city": city,
        "temperature": ":field temperature:",
        "weathercode": ":field weathercode:",
        "windspeed": ":field windspeed:",
        "winddirection": ":field winddirection:",
        "time": ":field time:",
    }
    latest_data = semi(f"fetch latest weather for {city} with {FETCH(city, output_format=weather_format)}")
    if "error" in latest_data:
        raise ValueError(latest_data["error"])
    
    try:
        combined = pd.concat([user_data.table(), latest_data], ignore_index=True)
    except Exception as e:
        combined = semi.concat([user_data.table(), latest_data], ignore_index=True)


    from semipy.tools import FETCH_WEATHER
    latest = FETCH_WEATHER(city)
    if "error" in latest:
        raise ValueError(latest["error"])
    table = user_data.table()
    row = _map_latest_to_row(latest)
    missing = [c for c in table.columns if row.get(c) is None or (c not in row)]
    if missing:
        extra = _append_latest_row_semi(table.columns.tolist(), latest, missing)
        if isinstance(extra, dict):
            for c in missing:
                if c in extra:
                    row[c] = extra[c]
    row = {c: row.get(c, pd.NA) for c in table.columns}
    new_row_df = pd.DataFrame([row])
    combined = pd.concat([table, new_row_df], ignore_index=True)
    return WeatherDataset.from_table(combined, source_path=user_data.source_path)


def _map_latest_to_row(latest: dict[str, Any]) -> dict[str, Any]:
    """Formal mapping from FETCH_WEATHER dict to common weather table columns."""
    row = {}
    if "time" in latest:
        try:
            row["date"] = str(latest["time"])[:10]
        except Exception:
            row["date"] = latest["time"]
    if "temperature" in latest:
        row["temp_max"] = latest["temperature"]
        row["temp_min"] = latest["temperature"]
    if "windspeed" in latest:
        row["wind"] = latest["windspeed"]
    if "weathercode" in latest:
        row["weather"] = _weathercode_to_label(latest["weathercode"])
    row["precipitation"] = latest.get("precipitation")
    row["city"] = latest.get("city")
    return row


def _weathercode_to_label(code: Any) -> str:
    """Map WMO weather code to short label. Formal."""
    if code is None:
        return ""
    c = int(code) if isinstance(code, (int, float)) else 0
    if c == 0:
        return "clear"
    if c in (1, 2, 3):
        return "cloudy"
    if c in (45, 48):
        return "fog"
    if c in (51, 53, 55, 56, 57):
        return "drizzle"
    if c in (61, 63, 65, 66, 67):
        return "rain"
    if c in (71, 73, 75, 77):
        return "snow"
    if c in (80, 81, 82):
        return "rain"
    if c in (95, 96, 99):
        return "thunderstorm"
    return "unknown"


@semiformal("map a fetched weather dict to one row matching given column names when schema is unknown")
def _append_latest_row_semi(
    columns: list[str],
    latest: dict[str, Any],
    unmapped_columns: list[str],
    **kwargs: Any,
) -> Optional[dict[str, Any]]:
    """Produce a one-row dict matching columns. Semiformal: only when some columns cannot be predefined."""
    return semi(
        f"""Arguments in order: (columns, latest, unmapped_columns). Columns: {columns}. Fetched record: {latest}. Unmapped (must fill): {unmapped_columns}.
        Return a single dict with keys exactly columns, mapping record fields to columns; use None or NaN for missing. Return only the dict. No explanation."""
    )


@semiformal("preprocess a column without knowing its data type or semantics")
def preprocess_column(ds: WeatherDataset, column: str, **kwargs: Any) -> WeatherDataset:
    """Preprocess a column (normalize, coerce type, handle missing). Semiformal: data type unknown a priori."""
    if not ds.is_table():
        raise ValueError("preprocess_column requires a table dataset")
    tbl = ds.table()
    if column not in tbl.columns:
        raise ValueError(f"Column {column!r} not in dataset")
    series = tbl[column]
    sample = series.head(20).tolist()
    modified = semi(
        f"""First arg: series (pandas Series), second: column name. Column {column!r}; sample: {sample}.
        Return a new Series, same index and length as input, with this column preprocessed (coerce numeric, parse dates, or fill missing). Import pandas as pd. Args order: (series, column).
        series={series}, column={column}."""
    )
    if modified is None or not hasattr(modified, "__len__"):
        return ds
    if len(modified) != len(tbl):
        return ds
    new_tbl = tbl.assign(**{column: modified})
    return WeatherDataset.from_table(new_tbl, source_path=ds.source_path)
