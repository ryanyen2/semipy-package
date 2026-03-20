"""Generated implementations for session use_visualization_builder. Do not edit by hand."""
from __future__ import annotations

DISPATCH = {}

# slot: 6ff285e6bd05e0b4 | category: statement | commit: f0b9c254 | GENERATE | spec: Given {n} variables, decide subplot grid (rows, cols).
def SmartChart_infer_layout_slot_6ff285e6_f0b9c254(self, n):
    import math
    if n is None:
        n_int = 0
    else:
        try:
            n_int = int(n)
        except Exception:
            n_int = 0
    if n_int <= 0:
        cols = 0
        rows = 0
    else:
        cols = int(math.ceil(math.sqrt(n_int)))
        rows = int(math.ceil(n_int / cols)) if cols else 0
    return {"cols": cols, "rows": rows}

# slot: b753ed566e632225 | category: statement | commit: f790010f | GENERATE | spec: Infer axis display config for variable named "{key}" with sample values. Decide scale, label, and tick density
def SmartChart_infer_axis_config_slot_b753ed56_f790010f(self, key, values):
    key_str = "" if key is None else str(key)
    nums = []
    if values is not None:
        for v in values:
            if v is None:
                continue
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                continue
    if not nums:
        label = key_str
        scale = "linear"
        tick_density = 5
        return {"label": label, "scale": scale, "tick_density": tick_density}

    vmin = min(nums)
    vmax = max(nums)

    tokens = [t for t in key_str.strip().split("_") if t]
    unit_tokens = {"ppm", "pct", "percent", "%", "usd", "eur", "gbp", "c", "f", "kwh", "wh", "mw", "gw", "kg", "g", "mg", "t", "ton", "tons", "ms", "s", "min", "h", "hr", "hrs", "day", "days", "yr", "year", "years"}
    unit = None
    if tokens:
        last = tokens[-1].lower()
        if last in unit_tokens:
            unit = tokens.pop(-1)

    def fmt_token(t):
        tl = t.lower()
        if tl == "co2":
            return "CO2"
        if t.isalpha() and len(t) <= 4:
            return t.upper()
        if t.isdigit():
            return t
        return t.capitalize()

    base_label = " ".join(fmt_token(t) for t in tokens) if tokens else key_str
    if unit:
        unit_disp = unit
        if unit_disp == "pct" or unit_disp == "percent":
            unit_disp = "%"
        label = f"{base_label} ({unit_disp})"
    else:
        label = base_label

    scale = "linear"
    if "log" in key_str.lower():
        scale = "log"
    else:
        if vmin > 0:
            ratio = vmax / vmin if vmin != 0 else float("inf")
            if ratio >= 1000 and (vmax - vmin) > 0:
                scale = "log"

    n = len(nums)
    rng = vmax - vmin
    if scale == "log":
        tick_density = 4
    else:
        if rng == 0:
            tick_density = 3
        else:
            if all(float(x).is_integer() for x in nums):
                irng = int(round(rng))
                if 0 < irng <= 20:
                    tick_density = min(10, irng + 1)
                else:
                    tick_density = 6 if n >= 10 else 5
            else:
                if n >= 50:
                    tick_density = 8
                elif n >= 20:
                    tick_density = 7
                elif n >= 10:
                    tick_density = 6
                else:
                    tick_density = 5

    return {"label": label, "scale": scale, "tick_density": tick_density}

# slot: a7fdd9828f98a4b8 | category: standalone | commit: 332120d2 | GENERATE | spec: tick formatter object for '{v0}' with scale={v1}, density={v2}, range=[{v3}, {v4}]. Return a matplotlib.ticker.FuncFormatter axis-independent (do not use ScalarFormatter internals that require an axis...
def SmartChart_render_slot_a7fdd982_332120d2(v0, v1, v2, v3, v4):
    from matplotlib.ticker import Formatter, FuncFormatter
    import math

    key = v0 if isinstance(v0, str) else ""
    scale = v1 if isinstance(v1, str) else "linear"

    try:
        density = int(v2)
    except Exception:
        density = 0

    unit = ""
    for u in ("ppm", "K", "ms"):
        if key.endswith("_" + u) or key == u:
            unit = u
            break

    def _decimals_from_density(d):
        if d is None:
            return 0
        if d <= 0:
            return 0
        if d <= 2:
            return 0
        if d <= 4:
            return 1
        if d <= 7:
            return 2
        if d <= 10:
            return 3
        return 4

    decimals = _decimals_from_density(density)

    def _si_compact(val):
        av = abs(val)
        if av >= 1e12:
            return val / 1e12, "T"
        if av >= 1e9:
            return val / 1e9, "G"
        if av >= 1e6:
            return val / 1e6, "M"
        if av >= 1e3:
            return val / 1e3, "k"
        if av >= 1:
            return val, ""
        if av >= 1e-3:
            return val * 1e3, "m"
        if av >= 1e-6:
            return val * 1e6, "µ"
        if av >= 1e-9:
            return val * 1e9, "n"
        return val, ""

    def _format_number(val, decs):
        if decs <= 0:
            if abs(val) >= 1:
                return str(int(round(val)))
            return ("{:.0g}".format(val))
        s = ("{:,." + str(decs) + "f}").format(val)
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    def _formatter(x, pos=None):
        if x is None:
            return ""
        try:
            xf = float(x)
        except Exception:
            return ""
        if math.isnan(xf):
            return ""
        if math.isinf(xf):
            return "∞" if xf > 0 else "-∞"
        if xf == 0:
            return "0" + ((" " + unit) if unit else "")

        if scale == "log":
            axf = abs(xf)
            if (axf >= 1e4) or (axf < 1e-3):
                mant, exp = ("{:." + str(max(0, decimals)) + "e}").format(xf).split("e")
                mant = mant.rstrip("0").rstrip(".")
                exp_i = int(exp)
                base = mant + "e" + str(exp_i)
                return base + ((" " + unit) if unit else "")

        if unit:
            return _format_number(xf, decimals) + " " + unit

        scaled, suf = _si_compact(xf)
        return _format_number(scaled, decimals) + suf

    fmt = FuncFormatter(_formatter)
    return fmt


DISPATCH['6ff285e6bd05e0b4'] = 'SmartChart_infer_layout_slot_6ff285e6_f0b9c254'
DISPATCH['b753ed566e632225'] = 'SmartChart_infer_axis_config_slot_b753ed56_f790010f'
DISPATCH['a7fdd9828f98a4b8'] = 'SmartChart_render_slot_a7fdd982_332120d2'