"""Semiformal data wrangling: semantic operators on tabular data."""
from semipy import semiformal, semi
from typing import Any, Callable


class Frame:
    """Thin wrapper over list[dict]. The data itself is always concrete."""

    def __init__(self, data: list[dict]):
        self.data = list(data)
        self.columns = list(data[0].keys()) if data else []

    def __repr__(self):
        n = len(self.data)
        preview = self.data[:3]
        return f"Frame({n} rows, {self.columns})\n{preview}"

    def __len__(self):
        return len(self.data)

    def __iter__(self):
        return iter(self.data)

    def __getitem__(self, key):
        if isinstance(key, str):
            return [d.get(key) for d in self.data]
        return self.data[key]

    def head(self, n=5) -> "Frame":
        return Frame(self.data[:n])

    # ── Core operators: formal structure, semi() fills the gaps ──

    @semiformal("filter rows where column matches a semantic condition")
    def filter(self, column: str, condition: str, **kwargs) -> "Frame":
        if column not in self.columns:
            raise KeyError(f"Column '{column}' not in {self.columns}")

        sample = [d[column] for d in self.data[:15]]

        kept = []
        for row in self.data:
            matches: bool = semi(
                f"""does the value {repr(row[column])} satisfy
                the condition "{condition}"?
                column sample: {repr(sample)}
                extra context: {kwargs}"""
            )
            if matches:
                kept.append(row)

        return Frame(kept)

    @semiformal("extract parts of a column into new columns")
    def extract(self, column: str, into: dict[str, str], **kwargs) -> "Frame":
        if column not in self.columns:
            raise KeyError(f"Column '{column}' not in {self.columns}")

        sample = [d[column] for d in self.data[:10]]
        result = []

        for row in self.data:
            new_row = dict(row)
            for new_col, description in into.items():
                new_row[new_col] = semi(
                    f"""from the value {repr(row[column])},
                    extract: {description}.
                    column sample: {repr(sample)}
                    extra context: {kwargs}"""
                )
            result.append(new_row)

        return Frame(result)

    @semiformal("compute a new column from existing data")
    def derive(self, name: str, expr: str, **kwargs) -> "Frame":
        sample = self.data[:10]
        result = []

        for row in self.data:
            value = semi(
                f"""compute "{expr}" for this row.
                row: {row}
                available columns: {self.columns}
                sample rows: {repr(sample)}
                extra context: {kwargs}"""
            )
            result.append({**row, name: value})

        return Frame(result)

    @semiformal("group and aggregate data")
    def aggregate(self, group_by: str | list[str],
                  aggs: dict[str, str], **kwargs) -> "Frame":
        if isinstance(group_by, str):
            group_by = [group_by]

        # Formal: grouping logic is always the same
        groups: dict[tuple, list[dict]] = {}
        for row in self.data:
            key = tuple[Any | None, ...](row.get(k) for k in group_by)
            groups.setdefault(key, []).append(row)

        result = []
        for key, rows in groups.items():
            out = {k: v for k, v in zip[tuple[str, Any]](group_by, key)}
            sample_group = rows[:10]

            for out_col, agg_desc in aggs.items():
                out[out_col] = semi(
                    f"""aggregate these {len(rows)} rows: "{agg_desc}".
                    sample: {repr(sample_group)}
                    available columns: {self.columns}
                    extra context: {kwargs}"""
                )
            result.append(out)

        return Frame(result)

    @semiformal("cast a column to a target type/format")
    def cast(self, column: str, to: str, **kwargs) -> "Frame":
        sample = [d[column] for d in self.data[:10]]
        result = []

        for row in self.data:
            new_row = dict(row)
            new_row[column] = semi(
                f"""convert {repr(row[column])} to {to}.
                column sample: {repr(sample)}
                extra context: {kwargs}"""
            )
            result.append(new_row)

        return Frame(result)

    @semiformal("sort rows by column name")
    def sort(self, column: str, order: str = "ascending", **kwargs) -> "Frame":
        sample = [d[column] for d in self.data[:15]]

        decorated = []
        for i, row in enumerate(self.data):
            sort_key = semi(
                f"""produce a numeric sort key for {repr(row[column])}
                such that values sort in {order} order.
                column sample: {repr(sample)}
                extra context: {kwargs}"""
            )
            decorated.append((sort_key, i, row))

        decorated.sort(key=lambda t: t[0])
        return Frame([row for _, _, row in decorated])

    @semiformal("join two frames on a common column")
    def join(self, other: "Frame", on: str, **kwargs) -> "Frame":
        sample_left = self.data[:5]
        sample_right = other.data[:5]
        result = []

        for left_row in self.data:
            for right_row in other.data:
                matches: bool = semi(
                    f"""do these rows match on condition "{on}"?
                    left: {left_row}
                    right: {right_row}
                    left columns: {self.columns}
                    right columns: {other.columns}
                    sample left: {repr(sample_left)}
                    sample right: {repr(sample_right)}
                    extra context: {kwargs}"""
                )
                if matches:
                    result.append({**left_row, **right_row})

        return Frame(result) if result else Frame([])