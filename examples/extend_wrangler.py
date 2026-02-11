from wrangler import Frame
from semipy import semiformal, semi


# ── USER DEFINES THEIR OWN OPERATOR ──
# They use @semiformal + semi() just like the library builder.
# The only rule: return a Frame.

@semiformal
def deduplicate(frame: Frame, column: str, strategy: str = "keep first",
                **kwargs) -> Frame:
    """Remove semantically duplicate rows."""
    sample = [d[column] for d in frame.data[:15]]
    seen_keys = []
    kept = []

    for row in frame.data:
        normalized = semi(
            f"""normalize {repr(row[column])} to a canonical form
            for deduplication.
            column sample: {repr(sample)}
            extra context: {kwargs}"""
        )

        is_dup = any(
            semi(f"are these semantically the same? {repr(normalized)} vs {repr(s)}")
            for s in seen_keys
        )

        if not is_dup:
            seen_keys.append(normalized)
            kept.append(row)
        elif strategy == "keep last":
            # Formal: replace the matching row
            for i, k in enumerate(seen_keys):
                if semi(f"are these the same? {repr(normalized)} vs {repr(k)}"):
                    seen_keys[i] = normalized
                    kept[i] = row
                    break

    return Frame(kept)


@semiformal
def pivot(frame: Frame, index: str, columns: str, values: str,
          agg: str = "sum", **kwargs) -> Frame:
    """Pivot long to wide format."""
    # Formal: collect unique pivot values
    pivot_vals = sorted(set(d.get(columns) for d in frame.data))
    index_vals = sorted(set(d.get(index) for d in frame.data))

    result = []
    for idx_val in index_vals:
        out = {index: idx_val}
        group = [d for d in frame.data if d.get(index) == idx_val]

        for pv in pivot_vals:
            cell_rows = [d for d in group if d.get(columns) == pv]
            cell_values = [d.get(values) for d in cell_rows]

            if cell_values:
                out[str(pv)] = semi(
                    f"""aggregate {repr(cell_values)} using "{agg}".
                    extra context: {kwargs}"""
                )
            else:
                out[str(pv)] = semi(
                    f"""appropriate missing/zero value for agg="{agg}"
                    on column type seen in {repr(frame[values][:5])}"""
                )

        result.append(out)

    return Frame(result)


# ── USER ADDS TO Frame VIA MONKEY-PATCHING ──
# (or subclass, but patching is more Pythonic for quick use)

@semiformal
def anomalies(self, column: str, method: str = "auto", **kwargs) -> "Frame":
    """Flag rows with anomalous values in the given column."""
    sample = [d[column] for d in self.data[:20]]
    all_values = [d[column] for d in self.data if column in d]

    # Formal: compute stats that semi() can reference
    stats = {}
    numeric_vals = [v for v in all_values if isinstance(v, (int, float))]
    if numeric_vals:
        stats["mean"] = sum(numeric_vals) / len(numeric_vals)
        stats["min"] = min(numeric_vals)
        stats["max"] = max(numeric_vals)

    result = []
    for row in self.data:
        is_anomaly: bool = semi(
            f"""is {repr(row[column])} anomalous given this column?
            method: {method}
            stats: {stats}
            full sample: {repr(sample)}
            extra context: {kwargs}"""
        )
        result.append({**row, f"{column}_anomaly": is_anomaly})

    return Frame(result)

Frame.anomalies = anomalies


# ── Now use it all together: ──

logs = Frame([...])  # same log data as before

(logs
 .extract("entry", {
     "timestamp": "the datetime",
     "level": "log level",
     "message": "main message content",
 })
 .filter("level", "is an error")
 .derive("category", "classify as compute/storage/network/security/other")
 .anomalies("timestamp", method="unusual time gap between consecutive entries")
 .sort("timestamp", order="chronological")
 .aggregate(
     group_by="category",
     aggs={
         "count": "total errors",
         "timeline": "comma-separated list of timestamps",
         "has_anomaly": "whether any row had timestamp_anomaly=True",
     }
 ))