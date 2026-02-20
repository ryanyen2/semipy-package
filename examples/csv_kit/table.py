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
    """Resolve a category (e.g. 'numeric', 'date') to column names. Used only for select(like=...)."""
    return semi(f"column names from {columns} that match: {category}", expected_type=list)


@semiformal("filter rows by semantic condition")
def _filter_semantic(rows: list[dict[str, Any]], spec: str) -> list[dict[str, Any]]:
    """Keep rows that satisfy a semantic spec (e.g. outliers, duplicates). Used only for where_semantic()."""
    if not rows or not spec.strip():
        return rows
    sample = rows[: min(20, len(rows))]
    return [row for row in rows if semi.matches(row, spec, sample=sample)]


@semiformal("sort key for semantic order")
def _sort_key_semantic(row: dict[str, Any], meaning: str) -> Any:
    """Compute sort key when order is semantic (e.g. recency, importance), not just column value. Used only for sort_semantic()."""
    return semi.sort_key(row, by=meaning)


@semiformal("merge two tables by semantic row matching")
def _merge_semantic_rows(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
    left_cols: list[str],
    right_cols: list[str],
    how: str,
) -> list[dict[str, Any]]:
    """Match rows from left and right by meaning (how). Returns list of merged row dicts."""
    return semi.merge_tables(left_rows, right_rows, left_columns=left_cols, right_columns=right_cols, how=how)


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

    def __init__(self, data: Union[pd.DataFrame, list[dict[str, Any]]], source_path: Optional[Path] = None) -> None:
        if isinstance(data, pd.DataFrame):
            self._df = data.copy()
        else:
            self._df = _df_from_rows(data)
        self._source_path = source_path

    @property
    def columns(self) -> list[str]:
        return list(self._df.columns)

    def _rows(self) -> list[dict[str, Any]]:
        return _rows_from_df(self._df)

    def select(self, *columns: str, like: Optional[str] = None) -> SemiTable:
        """
        Select columns. Pass column names: select("date", "price").
        If like is set (e.g. like="numeric"), resolve column set by meaning; only then semi is used.
        """
        if like is not None:
            cols = _columns_like(self.columns, like)
            cols = [c for c in cols if c in self._df.columns]
        else:
            cols = [c for c in columns if c in self._df.columns]
        if not cols:
            return SemiTable(pd.DataFrame(), source_path=self._source_path)
        return SemiTable(self._df[cols].copy(), source_path=self._source_path)

    def sort(self, by: str, order: Literal["asc", "desc"] = "asc") -> SemiTable:
        """Sort by column name. order is 'asc' or 'desc'. Fully deterministic."""
        if by not in self._df.columns:
            return SemiTable(self._df.copy(), source_path=self._source_path)
        ascending = order != "desc"
        out = self._df.sort_values(by=by, ascending=ascending).copy()
        return SemiTable(out, source_path=self._source_path)

    def sort_semantic(self, meaning: str) -> SemiTable:
        """
        Sort by semantic order (e.g. recency, importance), not by a single column value.
        Uses semi only here.
        """
        rows = self._rows()
        if not rows or not meaning.strip():
            return SemiTable(self._df.copy(), source_path=self._source_path)
        keyed = [(_sort_key_semantic(row, meaning), row) for row in rows]
        keyed.sort(key=lambda p: p[0])
        sorted_rows = [row for _, row in keyed]
        return SemiTable(_df_from_rows(sorted_rows, self.columns), source_path=self._source_path)

    def where(self, **kwargs: Any) -> SemiTable:
        """
        Filter by column predicates. Formal: where(price__gt=50, region="North").
        Use where_semantic() for predicates that are not column-op-value.
        """
        out = _apply_where_formal(self._df, **kwargs)
        return SemiTable(out, source_path=self._source_path)

    def where_semantic(self, spec: str) -> SemiTable:
        """Filter by a semantic spec (e.g. 'outliers', 'duplicates'). Uses semi only here."""
        rows = self._rows()
        filtered = _filter_semantic(rows, spec)
        return SemiTable(_df_from_rows(filtered, self.columns), source_path=self._source_path)

    def merge(self, other: SemiTable, on: Union[str, list[str]]) -> SemiTable:
        """Join with other table on column(s). Formal, deterministic."""
        if isinstance(on, str):
            on = [on]
        common = [c for c in on if c in self._df.columns and c in other._df.columns]
        if not common:
            return SemiTable(self._df.copy(), source_path=self._source_path)
        merged = self._df.merge(other._df, on=common, how="inner", suffixes=("", "_right"))
        merged = merged[[c for c in merged.columns if not c.endswith("_right")]]
        return SemiTable(merged, source_path=self._source_path)

    def merge_semantic(self, other: SemiTable, how: str) -> SemiTable:
        """
        Merge by matching rows semantically (e.g. how="match by meaning of key columns").
        Uses semi only here.
        """
        left_rows = self._rows()
        right_rows = other._rows()
        left_cols = self.columns
        right_cols = other.columns
        if not left_rows or not right_rows:
            return SemiTable(self._df.copy(), source_path=self._source_path)
        try:
            result_rows = _merge_semantic_rows(left_rows, right_rows, left_cols, right_cols, how)
        except Exception:
            return SemiTable(self._df.copy(), source_path=self._source_path)
        if not result_rows:
            return SemiTable(pd.DataFrame(), source_path=self._source_path)
        return SemiTable(_df_from_rows(result_rows), source_path=self._source_path)

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


def open_table(path: Union[str, Path]) -> SemiTable:
    """Load a CSV file into a SemiTable."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    df = pd.read_csv(p)
    return SemiTable(df, source_path=p)
