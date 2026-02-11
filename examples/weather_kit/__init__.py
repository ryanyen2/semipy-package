"""Weather analysis and visualization: semi for inference, formal for structure.

Formal: open_dataset(), WeatherDataset, plot_map structure.
Semi: pick_date_column, fetch_weather, map_fetched_weather_to_row, preprocess_series (no patterns or conditionals).
"""

from weather_kit.dataset import WeatherDataset, open_dataset
from weather_kit.ops import (
    infer_date_column,
    latest_append,
    plot_map,
    plot_timeseries,
    preprocess_column,
)

__all__ = [
    "WeatherDataset",
    "open_dataset",
    "plot_map",
    "plot_timeseries",
    "latest_append",
    "infer_date_column",
    "preprocess_column",
]
