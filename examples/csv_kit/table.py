"""SemiTable: tabular API that looks deterministic. Malleability only where needed.

User writes code like spec: select("date", "price"), sort(by="price", order="desc"),
where(price__gt=50). Semi is used only inside a few operations that cannot be
fully determined from column names and values: e.g. "which columns are numeric?",
"which rows are outliers?", "sort by recency", "merge by matching meaning".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional, Union

import pandas as pd

from semipy import semiformal, semi
from semipy.reactivity import _flow_from_inputs


def _rows_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    return df.to_dict("records")


def _df_from_rows(rows: list[dict[str, Any]], columns: Optional[list[str]] = None) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=columns or [])
    return pd.DataFrame(rows, columns=columns or list(rows[0].keys()))


# ---------------------------------------------------------------------------
# Semi only where implementation cannot be prebuilt
# ---------------------------------------------------------------------------

@semiformal("column names matching semantic category")
def _columns_like(columns: list[str], category: str) -> list[str]:
    return semi(f"column names from {columns} that match: {category}", expected_type=list)


@semiformal("filter rows by semantic condition")
def _filter_semantic(rows: list[dict[str, Any]], spec: str) -> list[dict[str, Any]]:
    if not rows or not spec.strip():
        return rows
    sample = rows[: min(20, len(rows))]
    return [row for row in rows if semi(f"row {row} matches semantic condition {spec}", expected_type=bool)]


@semiformal
def _sort_key_semantic(row: dict[str, Any], meaning: str) -> Any:
    """Compute sort key when order is semantic (e.g. recency, importance), not just column value. Used only for sort_semantic()."""
    return semi(
        f"Comparable sort key for table row {row!r} when user ordering intent is: {meaning!r}. "
        f"Return one Python value suitable for ascending sort (smaller means earlier in order).",
        expected_type=Any,
    )


@semiformal
def _merge_semantic_rows(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
    left_cols: list[str],
    right_cols: list[str],
    how: str,
) -> list[dict[str, Any]]:
    return semi(
        f"Merge two tabular row lists semantically. Left columns {left_cols!r}, right columns {right_cols!r}, "
        f"intent {how!r}. Left row count {len(left_rows)}, right {len(right_rows)}. "
        f"Return a list of dicts joining rows that refer to the same entity (e.g. same geography and date); "
        f"union of keys from both sides per matched row.",
        expected_type=list,
    )


@semiformal("apply extra parameters to table operation result")
def _apply_extra(rows: list[dict[str, Any]], columns: list[str], operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return semi(f"apply {params} to result of {operation} on table with columns {columns}", expected_type=list)


@semiformal("compute one new column value per row from a semantic spec")
def _compute_column(rows: list[dict[str, Any]], columns: list[str], spec: str) -> list[Any]:
    return semi(f"for each row compute a single value: {spec}. columns: {columns}. return a list of values, one per row, same length as rows.", expected_type=list)


_FORMAL_ORDER_TOKENS = frozenset({"asc", "desc", "ascending", "descending"})


# ---------------------------------------------------------------------------
# Formal predicate: parse where(price__gt=50, region="North")
# ---------------------------------------------------------------------------

def _apply_where_formal(df: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
    if not kwargs:
        return df
    mask = pd.Series(True, index=df.index)
    for key, value in kwargs.items():
        if "__" in key:
            col, op = key.rsplit("__", 1)
            if col not in df.columns:
                continue
            s = df[col]
            if op == "gt":
                mask &= s > value
            elif op == "gte":
                mask &= s >= value
            elif op == "lt":
                mask &= s < value
            elif op == "le":
                mask &= s <= value
            elif op == "eq":
                mask &= s == value
            elif op == "ne":
                mask &= s != value
            elif op == "in":
                mask &= s.isin(value if isinstance(value, (list, tuple)) else [value])
            elif op == "contains":
                mask &= s.astype(str).str.contains(str(value), case=False, na=False)
            else:
                continue
        else:
            if key in df.columns:
                mask &= df[key] == value
    return df[mask].copy()


# ---------------------------------------------------------------------------
# SemiTable: spec-style API; semi only in semantic branches
# ---------------------------------------------------------------------------

class SemiTable:
    """
    Tabular data: .select(), .sort(), .where(), .merge(), .show().
    API is deterministic and spec-like. Semiformal implementation is used only
    for select(like=...), where_semantic(), sort_semantic(), merge_semantic().
    """

    def __init__(
        self,
        data: Union[pd.DataFrame, list[dict[str, Any]]],
        source_path: Optional[Path] = None,
        _flow: Optional[Any] = None,
    ) -> None:
        if isinstance(data, pd.DataFrame):
            self._df = data.copy()
        else:
            self._df = _df_from_rows(data)
        self._source_path = source_path
        self._semi_flow = _flow

    @property
    def columns(self) -> list[str]:
        return list(self._df.columns)

    def _rows(self) -> list[dict[str, Any]]:
        return _rows_from_df(self._df)

    def select(self, *columns: str, like: Optional[str] = None, **extra: Any) -> SemiTable:
        """
        Select columns. Exact column names are used when present; otherwise semantic match via like.
        If like is set (e.g. like="numeric"), resolve column set by meaning.
        """
        if like is not None:
            cols = _columns_like(self.columns, like)
            cols = [c for c in cols if c in self._df.columns]
        else:
            cols = []
            for name in columns:
                if name in self._df.columns:
                    cols.append(name)
                else:
                    resolved = _columns_like(self.columns, name)
                    if resolved:
                        cols.append(resolved[0])
        flow = getattr(self, "_semi_flow", None)
        if not cols:
            out = SemiTable(pd.DataFrame(), source_path=self._source_path, _flow=flow)
        else:
            out = SemiTable(self._df[cols].copy(), source_path=self._source_path, _flow=flow)
        if extra:
            rows = out._rows()
            rows = _apply_extra(rows, out.columns, "select", extra)
            out = SemiTable(_df_from_rows(rows, out.columns), source_path=self._source_path, _flow=flow)
        return out

    def sort(self, by: str, order: Union[str, Literal["asc", "desc"]] = "asc", **extra: Any) -> SemiTable:
        """
        Sort by column. If order is asc/desc/ascending/descending use formal sort; else semantic (order as meaning).
        """
        flow = getattr(self, "_semi_flow", None)
        order_norm = str(order).strip().lower()
        if order_norm in _FORMAL_ORDER_TOKENS:
            if by not in self._df.columns:
                out = SemiTable(self._df.copy(), source_path=self._source_path, _flow=flow)
            else:
                ascending = order_norm in ("asc", "ascending")
                out = SemiTable(self._df.sort_values(by=by, ascending=ascending).copy(), source_path=self._source_path, _flow=flow)
        else:
            rows = self._rows()
            if not rows or not order_norm:
                out = SemiTable(self._df.copy(), source_path=self._source_path, _flow=flow)
            else:
                keyed = [(_sort_key_semantic(row, str(order)), row) for row in rows]
                keyed.sort(key=lambda p: p[0])
                sorted_rows = [row for _, row in keyed]
                out = SemiTable(_df_from_rows(sorted_rows, self.columns), source_path=self._source_path, _flow=flow)
        if extra:
            rows = out._rows()
            rows = _apply_extra(rows, out.columns, "sort", extra)
            out = SemiTable(_df_from_rows(rows, out.columns), source_path=self._source_path, _flow=flow)
        return out

    def sort_semantic(self, meaning: str) -> SemiTable:
        """Thin wrapper: sort by semantic order (delegates to sort(..., order=meaning))."""
        by = self.columns[0] if self.columns else ""
        return self.sort(by=by, order=meaning)

    def where(self, *conditions: Union[str, Any], **kwargs: Any) -> SemiTable:
        """
        Filter: formal kwargs (col__op or column name) applied first, then positional string conditions semantically.
        """
        flow = getattr(self, "_semi_flow", None)
        formal_kwargs = {k: v for k, v in kwargs.items() if "__" in k or k in self._df.columns}
        extra = {k: v for k, v in kwargs.items() if k not in formal_kwargs}
        out = _apply_where_formal(self._df, **formal_kwargs)
        out = SemiTable(out, source_path=self._source_path, _flow=flow)
        for spec in conditions:
            if isinstance(spec, str) and spec.strip():
                rows = out._rows()
                rows = _filter_semantic(rows, spec)
                out = SemiTable(_df_from_rows(rows, out.columns), source_path=self._source_path, _flow=flow)
        if extra:
            rows = out._rows()
            rows = _apply_extra(rows, out.columns, "where", extra)
            out = SemiTable(_df_from_rows(rows, out.columns), source_path=self._source_path, _flow=flow)
        return out

    def where_semantic(self, spec: str) -> SemiTable:
        """Thin wrapper: filter by semantic spec (delegates to where(spec))."""
        return self.where(spec)

    def merge(self, other: SemiTable, on: Optional[Union[str, list[str]]] = None, how: Optional[str] = None, **extra: Any) -> SemiTable:
        """
        Join: if on is provided use formal merge; if how is provided (no on) use semantic row matching.
        """
        merged_flow = _flow_from_inputs(self, other)
        if on is not None:
            on_list = [on] if isinstance(on, str) else list(on)
            common = [c for c in on_list if c in self._df.columns and c in other._df.columns]
            if not common:
                out = SemiTable(self._df.copy(), source_path=self._source_path, _flow=merged_flow)
            else:
                merged = self._df.merge(other._df, on=common, how="inner", suffixes=("", "_right"))
                merged = merged[[c for c in merged.columns if not c.endswith("_right")]]
                out = SemiTable(merged, source_path=self._source_path, _flow=merged_flow)
        elif how is not None:
            left_rows = self._rows()
            right_rows = other._rows()
            if not left_rows or not right_rows:
                out = SemiTable(self._df.copy(), source_path=self._source_path, _flow=merged_flow)
            else:
                try:
                    result_rows = _merge_semantic_rows(left_rows, right_rows, self.columns, other.columns, how)
                    out = SemiTable(_df_from_rows(result_rows), source_path=self._source_path, _flow=merged_flow)
                except Exception:
                    out = SemiTable(self._df.copy(), source_path=self._source_path, _flow=merged_flow)
        else:
            out = SemiTable(self._df.copy(), source_path=self._source_path, _flow=merged_flow)
        if extra:
            rows = out._rows()
            rows = _apply_extra(rows, out.columns, "merge", extra)
            out = SemiTable(_df_from_rows(rows, out.columns), source_path=self._source_path, _flow=merged_flow)
        return out

    def merge_semantic(self, other: SemiTable, how: str) -> SemiTable:
        """Thin wrapper: merge by semantic row matching (delegates to merge(other, how=how))."""
        return self.merge(other, how=how)

    def show(self, n: int = 10) -> str:
        """Return a string view of the first n rows. Deterministic."""
        rows = self._rows()
        cols = self.columns
        if not cols:
            return "(empty table)"
        head = rows[:n]
        if not head:
            return "(no rows)"
        lines = [" | ".join(cols)]
        lines.append("-" * 50)
        for row in head:
            line = " | ".join(str(row.get(c, "")) for c in cols)
            lines.append(line)
        if len(rows) > n:
            lines.append(f"... ({len(rows) - n} more rows)")
        return "\n".join(lines)

    def to_dataframe(self) -> pd.DataFrame:
        return self._df.copy()

    def assign_semantic(self, **column_specs: str) -> SemiTable:
        """
        Add new columns from semantic specs. Each key is the new column name;
        each value is a string spec (e.g. "1 when market cap above 1000 else 0").
        Semi interprets the spec over table rows and returns one value per row.
        """
        flow = getattr(self, "_semi_flow", None)
        if not column_specs:
            return SemiTable(self._df.copy(), source_path=self._source_path, _flow=flow)
        rows = self._rows()
        cols = self.columns
        out_df = self._df.copy()
        for col_name, spec in column_specs.items():
            if not isinstance(spec, str) or not spec.strip():
                continue
            values = _compute_column(rows, cols, spec)
            if isinstance(values, list) and len(values) == len(out_df):
                out_df[col_name] = values
        return SemiTable(out_df, source_path=self._source_path, _flow=flow)


def open_table(path: Union[str, Path]) -> SemiTable:
    """Load a CSV file into a SemiTable."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    df = pd.read_csv(p)
    return SemiTable(df, source_path=p)


class CovidReportBuilder:
    """
    Hybrid @semiformal usage: formal filtering and iteration, open regions for prose and labels.
    Mirrors patterns where most code is deterministic and only some statements are underspecified.
    """

    def __init__(self, tbl: SemiTable) -> None:
        self._tbl = tbl

    @semiformal
    def narrative_opening(self, confirmed_floor: int) -> str:
        subset = self._tbl.where(Confirmed__gte=confirmed_floor)
        n = len(subset.to_dataframe())
        #> Write one opening sentence for a briefing that filtered rows to at least `confirmed_floor` confirmed cases and currently has `n` rows after that filter. Keep wording generic (public health table), not tied to one country.

        return opening

    @semiformal
    def column_captions(self, max_cols: int) -> dict[str, str]:
        names = self._tbl.columns[: max_cols]
        out: dict[str, str] = {}
        for name in names:
            caption: str = semi(
                f"Short human-facing caption for a table column named {name!r} in a disease surveillance dataset.",
                expected_type=str,
            )
            out[name] = caption
        return out
