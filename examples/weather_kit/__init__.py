"""Weather analysis and visualization: formal + semiformal.

Formal layer: open_dataset(), WeatherDataset (grid/table), deterministic ops.
Semiformal layer: plot_map(), summarize(), ask() — intent-driven, generated code
uses the formal API and tools (e.g. FETCH_WEATHER). Like Herbie, but with
underspecified steps filled by generation.

  from weather_kit import open_dataset, plot_map, current_summary

  ds = open_dataset("data/seattle-weather.csv")   # formal: load
  plot_map(ds, "temperature")                       # semiformal: plot (if grid)
  summary = current_summary("Seattle")               # semiformal: fetch + summarize
"""

from weather_kit.dataset import WeatherDataset, open_dataset
from weather_kit.semiformal_ops import (
    current_summary,
    plot_map,
    plot_timeseries,
    ask,
)

__all__ = [
    "WeatherDataset",
    "open_dataset",
    "plot_map",
    "plot_timeseries",
    "current_summary",
    "ask",
]
