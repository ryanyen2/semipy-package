"""Weather analysis and visualization: semi for inference, formal for structure.

Formal: open_dataset(), WeatherDataset, plot_map layout, concat after fetch.
Semi: inline semi(\"...\", expected_type=...) inside @semiformal for date column, HTTP
weather fetch, column alignment, preprocessing, and tick formatting. Fetch/scrape
is described in the prompt so the agentic pipeline can emit HTTP client code.
"""

from weather_kit.dataset import WeatherDataset, open_dataset
from weather_kit.ops import (
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
    "preprocess_column",
]
