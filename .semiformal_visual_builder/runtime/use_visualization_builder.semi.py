"""Generated implementations for session use_visualization_builder. Do not edit by hand."""
from __future__ import annotations

DISPATCH = {}

# slot: 6ff285e6bd05e0b4 | category: statement | commit: 3528627e | GENERATE | spec: Given {n} variables, decide subplot grid (rows, cols).
def SmartChart_infer_layout_slot_6ff285e6_3528627e(self, n) -> dict:
    try:
        n_int = int(n)
    except Exception:
        n_int = 0
    if n_int <= 0:
        return {"cols": 0, "rows": 0}

    import math

    cols = int(math.ceil(math.sqrt(n_int)))
    rows = int(math.ceil(n_int / cols)) if cols > 0 else 0
    return {"cols": cols, "rows": rows}

# slot: b753ed566e632225 | category: statement | commit: ca87f297 | GENERATE | spec: Infer axis display config for variable named "{key}" with sample values. Decide scale, label, and tick density
def SmartChart_infer_axis_config_slot_b753ed56_ca87f297(self, key, values) -> dict[str, object]:
    def _to_float(x):
        try:
            if x is None:
                return None
            if isinstance(x, bool):
                return float(int(x))
            return float(x)
        except Exception:
            return None

    def _is_finite(x: float) -> bool:
        return x == x and x not in (float("inf"), float("-inf"))

    def _smart_token(tok: str) -> str:
        t = tok.strip()
        if not t:
            return t
        low = t.lower()
        if any(ch.isdigit() for ch in t) or len(t) <= 3:
            return low.upper()
        return low[:1].upper() + low[1:]

    key_str = "" if key is None else str(key).strip()
    parts = [p for p in key_str.replace("-", "_").split("_") if p]

    unit_map = {
        "ppm": "ppm",
        "ppb": "ppb",
        "pct": "%",
        "percent": "%",
        "percentage": "%",
        "usd": "USD",
        "eur": "EUR",
        "gbp": "GBP",
        "kg": "kg",
        "g": "g",
        "mg": "mg",
        "t": "t",
        "ton": "t",
        "tons": "t",
        "m": "m",
        "cm": "cm",
        "mm": "mm",
        "km": "km",
        "s": "s",
        "sec": "s",
        "secs": "s",
        "ms": "ms",
        "h": "h",
        "hr": "h",
        "hrs": "h",
        "day": "day",
        "days": "day",
        "yr": "yr",
        "yrs": "yr",
        "year": "yr",
        "years": "yr",
        "c": "°C",
        "f": "°F",
        "k": "K",
    }

    unit = None
    if parts:
        last = parts[-1].lower()
        if last in unit_map:
            unit = unit_map[last]
            parts = parts[:-1]

    if parts:
        core = " ".join(_smart_token(p) for p in parts)
    else:
        core = _smart_token(key_str) if key_str else ""

    label = core
    if unit:
        label = f"{core} ({unit})" if core else f"({unit})"
    if not label:
        label = key_str or ""

    nums = []
    if isinstance(values, (list, tuple)):
        for v in values:
            fv = _to_float(v)
            if fv is None:
                continue
            if _is_finite(fv):
                nums.append(fv)

    scale = "linear"
    if nums:
        vmin = min(nums)
        vmax = max(nums)
        pos_min = min((x for x in nums if x > 0), default=None)
        if "log" in key_str.lower():
            scale = "log"
        elif pos_min is not None and vmax > 0:
            ratio = vmax / pos_min if pos_min > 0 else float("inf")
            if ratio >= 1000:
                scale = "log"
            else:
                absmax = max(abs(vmin), abs(vmax), 1e-12)
                span = vmax - vmin
                if absmax > 0 and (span / absmax) > 0.99 and ratio >= 100:
                    scale = "log"

    n = len(nums)
    unique_ratio = 0.0
    if n:
        try:
            unique_ratio = len(set(nums)) / n
        except Exception:
            unique_ratio = 1.0

    if n <= 0:
        tick_density = "medium"
    elif scale == "log":
        tick_density = "sparse"
    elif n <= 5 or unique_ratio <= 0.35:
        tick_density = "sparse"
    elif n <= 25:
        tick_density = "medium"
    else:
        tick_density = "dense"

    return {"label": label, "scale": scale, "tick_density": tick_density}

# slot: 393463958ea98497 | category: standalone | commit: 61244b5d | GENERATE | spec: tick formatter object for '{v0}' with scale='linear', density='medium', range=[280, 480]. Return a matplotlib.ticker.FuncFormatter axis-independent (do not use ScalarFormatter internals that require a...
def SmartChart_render_slot_39346395_61244b5d(v0):
    from matplotlib.ticker import Formatter, FuncFormatter
    import math

    key = (v0 or "").strip()

    unit = ""
    if key.endswith("_ppm"):
        unit = " ppm"
    elif key.endswith("_K"):
        unit = " K"
    elif key.endswith("_ms"):
        unit = " ms"

    def _is_finite_number(x):
        try:
            xf = float(x)
        except Exception:
            return False, None
        if math.isnan(xf) or math.isinf(xf):
            return False, None
        return True, xf

    def _strip_zeros(s):
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    def _fmt(x, pos=None):
        ok, xf = _is_finite_number(x)
        if not ok:
            return ""
        ax = abs(xf)

        if ax >= 1e6:
            val, suf, dec = xf / 1e6, "M", 1
        elif ax >= 1e3:
            val, suf, dec = xf / 1e3, "k", 1
        else:
            val, suf = xf, ""
            if ax >= 100:
                dec = 0
            elif ax >= 10:
                dec = 1
            else:
                dec = 2

        if dec <= 0:
            s = f"{val:,.0f}"
        else:
            s = _strip_zeros(f"{val:,.{dec}f}")
        return f"{s}{suf}{unit}"

    fmt = FuncFormatter(_fmt)
    return fmt

# slot: 0139ca78b18d72d9 | category: standalone | commit: ad6e7f3e | GENERATE | spec: tick formatter object for '{v0}' with scale='linear', density='medium', range=[287, 321]. Return a matplotlib.ticker.FuncFormatter axis-independent (do not use ScalarFormatter internals that require a...
def SmartChart_render_slot_0139ca78_ad6e7f3e(v0):
    from matplotlib.ticker import FuncFormatter, Formatter
    import math

    key = v0 or ""

    def _infer_unit(k: str):
        units = {
            "_ppm": "ppm",
            "_ppb": "ppb",
            "_K": "K",
            "_C": "°C",
            "_F": "°F",
            "_ms": "ms",
            "_s": "s",
            "_min": "min",
            "_hr": "h",
            "_day": "d",
            "_m": "m",
            "_km": "km",
            "_%": "%",
        }
        for suf, u in units.items():
            if k.endswith(suf):
                return u, suf
        return "", ""

    unit, _ = _infer_unit(key)

    density = "medium"
    decimals_by_density = {"dense": 3, "medium": 2, "sparse": 0}
    max_decimals = decimals_by_density.get(density, 2)

    def _format_number(val: float) -> str:
        if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
            return ""
        try:
            x = float(val)
        except Exception:
            return ""

        ax = abs(x)

        if max_decimals <= 0:
            if ax < 1e6:
                s = f"{int(round(x))}"
            else:
                s = f"{x:.0f}"
            return s

        if ax == 0:
            return "0"

        is_intish = abs(x - round(x)) < 10 ** (-(max_decimals + 1))
        if is_intish:
            return str(int(round(x)))

        if ax < 1e-3:
            dec = min(max_decimals + 2, 6)
        elif ax < 1:
            dec = min(max_decimals + 1, 6)
        elif ax < 10:
            dec = max_decimals
        elif ax < 100:
            dec = max(0, max_decimals - 1)
        else:
            dec = max(0, max_decimals - 2)

        s = f"{x:.{dec}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    def _si_compact(x: float):
        prefixes = [
            (1e12, "T"),
            (1e9, "G"),
            (1e6, "M"),
            (1e3, "k"),
            (1.0, ""),
            (1e-3, "m"),
            (1e-6, "µ"),
            (1e-9, "n"),
        ]
        ax = abs(x)
        for scale, p in prefixes:
            if ax >= scale or scale == 1e-9:
                return x / scale, p
        return x, ""

    def _fmt(x, pos=None):
        if x is None:
            return ""

        if unit in {"ppm", "ppb", "%", "K", "°C", "°F", "ms", "s", "min", "h", "d", "m", "km"}:
            num = _format_number(x)
            return f"{num}{(' ' + unit) if unit and unit not in {'%'} else unit}"

        try:
            xf = float(x)
        except Exception:
            return ""
        y, p = _si_compact(xf)
        num = _format_number(y)
        return f"{num}{p}"

    fmt = FuncFormatter(_fmt)
    return fmt


DISPATCH['6ff285e6bd05e0b4'] = 'SmartChart_infer_layout_slot_6ff285e6_3528627e'
DISPATCH['b753ed566e632225'] = 'SmartChart_infer_axis_config_slot_b753ed56_ca87f297'
DISPATCH['393463958ea98497'] = 'SmartChart_render_slot_39346395_61244b5d'
DISPATCH['0139ca78b18d72d9'] = 'SmartChart_render_slot_0139ca78_ad6e7f3e'