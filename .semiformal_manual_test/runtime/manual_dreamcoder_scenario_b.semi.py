"""Generated implementations for session manual_dreamcoder_scenario_b. Do not edit by hand."""
from __future__ import annotations

DISPATCH = {}

# slot: NoneType_analyze | commit: 4487237d | GENERATE
def NoneType_analyze_4487237d(v1):
    threshold = v1

    def _apply(df):
        if df is None:
            return df
        try:
            if hasattr(df, "columns") and hasattr(df, "loc"):
                if "year" not in getattr(df, "columns", []):
                    return df
                years = df["year"]
                try:
                    mask = years <= threshold
                except Exception:
                    mask = [y <= threshold for y in list(years)]
                try:
                    return df.loc[mask]
                except Exception:
                    return df[mask]
        except Exception:
            pass

        if isinstance(df, (list, tuple)):
            out = []
            for row in df:
                if isinstance(row, dict):
                    y = row.get("year")
                    if y is None or not isinstance(y, (int, float)):
                        out.append(row)
                    elif y <= threshold:
                        out.append(row)
                else:
                    out.append(row)
            return out if isinstance(df, list) else tuple(out)

        if isinstance(df, dict):
            y = df.get("year")
            if isinstance(y, (int, float)) and y > threshold:
                return {}
            return df

        return df

    return _apply


DISPATCH['ba07b99d84953499:9954bcf368157ab4'] = 'NoneType_analyze_4487237d'