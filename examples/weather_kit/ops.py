"""Semiformal layer: intent-driven plot and analyze.

Generated code uses the formal WeatherDataset API and tools (FETCH_WEATHER).
Each operation is a partial program: you say what you want; the system
generates the code that does it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from semipy import semiformal, semi

from weather_kit.dataset import WeatherDataset


@semiformal("plot a 2D map of the dataset variable using matplotlib")
def plot_map(
    ds: WeatherDataset,
    variable: Optional[str] = None,
    title: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Plot this dataset as a 2D map. For grid data: pcolormesh of the variable.

    If variable is None, use the first 2D variable. Optional title.
    Returns the matplotlib figure or None. Use ds.get_2d(variable) for (lon, lat, values).
    """
    if not ds.is_grid():
        raise ValueError("Dataset is not a grid.")
    else:
        return semi(
            f"""Arguments in order: (ds, variable, title). ds is a grid WeatherDataset; variable is the 2D variable name (str or None); title is optional str.
            Use ds = ds.subset_for_plot() if grid is large. Then lon, lat, values = ds.get_2d(variable). Plot with matplotlib: pcolormesh(lon, lat, values), colorbar(), set title if provided. Return plt.gcf() or None. Import matplotlib.pyplot as plt.
            Context: ds={ds}, variable={variable}, title={title}."""
        )


@semiformal("plot a time series of a variable from tabular dataset")
def plot_timeseries(
    ds: WeatherDataset,
    variable: str,
    date_column: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Plot variable vs time. For table data with a date column.

    If date_column is None, infer (e.g. 'date', 'time'). Returns figure or None.
    """
    if not ds.is_table():
        return semi("Dataset is not tabular. Return None.")
    tbl = ds.table()
    cols = ", ".join(tbl.columns.tolist())
    return semi(
        f"""Arguments in order: (ds, variable, date_column). ds is tabular WeatherDataset; variable is the column to plot (y); date_column is optional (infer if None).
        Table columns: {cols}. Plot variable vs time. Use matplotlib, parse dates. Return plt.gcf() or None. Import matplotlib.pyplot as plt.
        Context: ds={ds}, variable={variable}, date_column={date_column}."""
    )


@semiformal("fetch current weather for a city and return a short summary string")
def current_summary(city: str, **kwargs: Any) -> str:
    """Fetch latest conditions for the city and return a readable summary.

    Uses {{FETCH_WEATHER(city)}}. The program cannot complete without the fetch.
    """
    return semi(
        f"""Call {{FETCH_WEATHER(city)}} for '{city}' and return a short readable summary
        (temperature, conditions, wind). Do not make up data."""
    )


@semiformal("answer a natural language question about current weather in a city")
def ask(city: str, question: str, **kwargs: Any) -> str | bool:
    """Answer a question about current weather in the city.

    Uses {{FETCH_WEATHER(city)}}. Return a short answer (str or bool).
    """
    return semi(
        f"""Call {{FETCH_WEATHER(city)}} for '{city}'. Answer: {question}
        Return a short string or True/False. Use only the fetched data."""
    )
