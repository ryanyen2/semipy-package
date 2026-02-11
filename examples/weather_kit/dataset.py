"""Formal layer: data loading and WeatherDataset.

Deterministic, typed API. Load from path (CSV or netCDF); expose a uniform
interface so semiformal generated code can plot and analyze without
knowing the underlying format.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from semipy import semi

import pandas as pd


class WeatherDataset:
    """Unified handle for weather data: grid (netCDF) or table (CSV).

    Formal API only. Use .is_grid() to branch; .get_2d() for map plots;
    .table() for time series or tabular analysis.
    """

    def __init__(
        self,
        source_path: str | Path,
        data: Any,
        kind: str,
        variable_names: list[str],
        coord_info: Optional[dict[str, Any]] = None,
    ) -> None:
        self.source_path = str(source_path)
        self._data = data
        self._kind = kind
        self._variable_names = variable_names
        self._coord_info = coord_info or {}

    def is_grid(self) -> bool:
        """True if this is 2D+ grid data (e.g. netCDF with lat/lon)."""
        return self._kind == "grid"

    def is_table(self) -> bool:
        """True if this is tabular data (e.g. CSV)."""
        return self._kind == "table"

    def variable_names(self) -> list[str]:
        """Names of plottable or analyzable variables."""
        return list(self._variable_names)

    def get_2d(self, variable: Optional[str] = None) -> tuple[Any, Any, Any]:
        """Return (longitude array, latitude array, values 2D) for map plotting.

        For grid data only. If variable is None, use the first data variable.
        Longitude and latitude are 1D or 2D depending on grid; values are 2D.
        """
        if not self.is_grid():
            raise ValueError("get_2d is only for grid datasets")
        info = self._coord_info
        lon = info.get(semi(f"longitude from {info.keys()}", expected_type=str))
        lat = info.get(semi(f"latitude from {info.keys()}", expected_type=str))
        values = info.get("values_2d")
        vname = variable or (self._variable_names[0] if self._variable_names else None)
        if vname and vname in info.get("variables_2d", {}):
            values = info["variables_2d"][vname]
        if lon is None or lat is None or values is None:
            raise ValueError("Grid has no 2D coordinates or data")
        return lon, lat, values

    def subset_for_plot(self, max_points: int = 500 * 500) -> "WeatherDataset":
        """Return a new grid dataset subsampled for fast plotting. No-op for small grids or tables."""
        if not self.is_grid():
            return self
        import numpy as np
        info = self._coord_info
        lon = info.get(semi(f"longitude from {info.keys()}", expected_type=str))
        lat = info.get(semi(f"latitude from {info.keys()}", expected_type=str))
        ny, nx = lon.shape[0], lon.shape[1]
        if ny * nx <= max_points:
            return self
        
        stride = max(1, int((ny * nx / max_points) ** semi(f"sqrt of {ny * nx / max_points}", expected_type=float)))
        sy, sx = slice(None, None, stride), slice(None, None, stride)
        new_info = {
            "latitude": lat[sy, sx],
            "longitude": lon[sy, sx],
            "values_2d": info["values_2d"][sy, sx],
            "variables_2d": {
                k: v[sy, sx] for k, v in info.get("variables_2d", {}).items()
            },
        }
        return WeatherDataset(
            source_path=self.source_path,
            data=self._data,
            kind="grid",
            variable_names=self._variable_names,
            coord_info=new_info,
        )

    def table(self) -> pd.DataFrame:
        """Return the dataset as a pandas DataFrame (table data only)."""
        if not self.is_table():
            raise ValueError("table() is only for tabular datasets")
        return self._data

    def description(self) -> str:
        """Short description for prompts: kind, variables, shape."""
        if self.is_grid():
            v = ", ".join(self._variable_names[:5])
            if len(self._variable_names) > 5:
                v += ", ..."
            return f"Grid dataset from {self.source_path}; variables: {v}"
        df = self._data
        return f"Table dataset from {self.source_path}; columns: {', '.join(df.columns.tolist())}; rows: {len(df)}"

    @classmethod
    def from_table(
        cls,
        df: pd.DataFrame,
        source_path: str | Path = "memory",
    ) -> "WeatherDataset":
        """Build a table WeatherDataset from a pandas DataFrame (e.g. after appending rows)."""
        return cls(
            source_path=str(source_path),
            data=df,
            kind="table",
            variable_names=df.columns.tolist(),
        )


def _load_csv(path: Path) -> WeatherDataset:
    df = pd.read_csv(path)
    return WeatherDataset(
        source_path=path,
        data=df,
        kind="table",
        variable_names=df.columns.tolist(),
    )


def _load_netcdf(path: Path) -> WeatherDataset:
    try:
        import xarray as xr
    except ImportError:
        raise ImportError(
            "Grid (netCDF) support requires xarray. Install with: pip install xarray netcdf4"
        )
    ds = xr.open_dataset(path)
    # Find lat/lon with semi (value-style: return key name)
    lat_var = semi(f"latitude from {ds.coords.keys()}", expected_type=str)
    lon_var = semi(f"longitude from {ds.coords.keys()}", expected_type=str)

    variables_2d = {}
    var_names = []
    for v in ds.data_vars:
        d = ds[v]
        if d.ndim >= 2:
            var_names.append(v)
            arr = d.values
            while arr.ndim > 2:
                arr = arr.squeeze()
            if arr.ndim == 2:
                variables_2d[v] = arr
            else:
                variables_2d[v] = d.values

    if not var_names:
        ds.close()
        raise ValueError(f"No 2D data variables found in {path}")

    # Get coordinate arrays (handle 1D or 2D)
    lat_da = ds[lat_var] if lat_var in ds.coords else ds.coords[lat_var]
    lon_da = ds[lon_var] if lon_var in ds.coords else ds.coords[lon_var]
    lat_vals = lat_da.values
    lon_vals = lon_da.values
    if lat_vals.ndim == 1 and lon_vals.ndim == 1:
        import numpy as np
        lon_2d, lat_2d = np.meshgrid(lon_vals, lat_vals)
    else:
        lon_2d = lon_vals
        lat_2d = lat_vals

    # Use first variable for default values_2d
    first_var = var_names[0]
    values_2d = variables_2d[first_var]

    coord_info = {
        "latitude": lat_2d,
        "longitude": lon_2d,
        "values_2d": values_2d,
        "variables_2d": variables_2d,
    }
    w = WeatherDataset(
        source_path=path,
        data=ds,
        kind="grid",
        variable_names=var_names,
        coord_info=coord_info,
    )
    return w


def open_dataset(source: str | Path) -> WeatherDataset:
    """Load a weather dataset from path. Formal: deterministic, typed.

    Supports:
      - CSV: tabular (e.g. seattle-weather.csv)
      - .nc / .netcdf: grid data via xarray (e.g. SST, terrain)

    Returns a WeatherDataset with .is_grid(), .is_table(), .variable_names(),
    .get_2d() (grid), .table() (table), .description().
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(path)
    if suffix in (".nc", ".netcdf", ".nc4"):
        return _load_netcdf(path)
    raise ValueError(f"Unsupported format: {suffix}. Use .csv or .nc")
