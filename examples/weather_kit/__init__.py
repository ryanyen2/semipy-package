"""Weather analysis and visualization: formal first, semiformal only when necessary.

Formal: open_dataset(), WeatherDataset, plot_map(), plot_timeseries(), latest_append (fetch + map).
Semiformal only for generalizability: infer_date_column(), schema-agnostic append, preprocess_column().
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
