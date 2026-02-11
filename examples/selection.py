"""Semiformal interactive selection library for Jupyter notebooks."""
from semipy import semiformal, semi
from typing import Any, Callable, Optional
from dataclasses import dataclass
import json
import html as html_lib
import random


@dataclass
class BrushConfig:
    fill: str = "rgba(100,150,250,0.3)"
    stroke: str = "#3366cc"
    stroke_width: float = 1

@dataclass
class MarkConfig:
    fill: str = "#4682b4"
    stroke: str = "#333"
    stroke_width: float = 1
    selected_fill: str = "#ff6347"
    selected_stroke: str = "#c00"
    size: float = 5


class Selection:
    """Tracks which data indices are currently selected by a brush."""

    def __init__(self, data: list[dict] = None):
        self._data: list[dict] = data or []
        self._selected: set[int] = set()
        self._listeners: list[Callable] = []

    def data(self, d: list[dict]) -> "Selection":
        self._data = d
        return self

    def on(self, fn: Callable) -> "Selection":
        self._listeners.append(fn)
        return self

    def update(self, indices: set[int]) -> "Selection":
        self._selected = {i for i in indices if 0 <= i < len(self._data)}
        self._notify()
        return self

    def clear(self) -> "Selection":
        self._selected.clear()
        self._notify()
        return self

    def _notify(self):
        for fn in self._listeners:
            fn(self)

    @property
    def indices(self) -> set[int]:
        return self._selected

    @property
    def values(self) -> list[dict]:
        return [self._data[i] for i in sorted(self._selected)
                if i < len(self._data)]

    def __repr__(self):
        return f"Selection({len(self._selected)}/{len(self._data)})"


# ── Scale: this is where semiformal shines ──
# Every chart library spends thousands of lines handling
# numeric vs temporal vs ordinal vs log scales.
# The formal structure (map domain→range) is always the same.
# The type-specific logic is what varies.

@semiformal
class Scale:
    """Maps data values to pixel coordinates and back."""

    def __init__(self, values: list, pixel_min: float, pixel_max: float,
                 invert: bool = False):
        self.values = values
        self.pixel_min = pixel_min
        self.pixel_max = pixel_max
        self.invert = invert

        # Formal: always compute these
        sample = random.sample(values, 20)

        # Semi: classify what kind of data this is
        self.dtype: str = semi(
            f"""classify these values as one of:
            'numeric', 'temporal', 'categorical', 'ordinal'.
            sample: {repr(sample)}"""
        )

        # Formal: compute domain based on classified type
        if self.dtype == "numeric":
            nums = [v for v in values if isinstance(v, (int, float))]
            self.domain = (min(nums), max(nums)) if nums else (0, 1)
        elif self.dtype == "categorical" or self.dtype == "ordinal":
            seen = []
            for v in values:
                if v not in seen:
                    seen.append(v)
            if self.dtype == "ordinal":
                seen = semi(
                    f"""sort these values in their natural order: {repr(seen)}.
                    return as a python list"""
                )
            self.domain = seen  # list of categories
        elif self.dtype == "temporal":
            self._parsed: list = [
                semi(f"parse '{v}' as a unix timestamp (float seconds)")
                for v in values
            ]
            self.domain = (min(self._parsed), max(self._parsed))

    def to_pixel(self, value: Any) -> float:
        """Map a data value to pixel position."""
        lo, hi = self.pixel_min, self.pixel_max
        if self.invert:
            lo, hi = hi, lo

        if self.dtype == "numeric":
            mn, mx = self.domain
            t = (value - mn) / (mx - mn + 1e-9)
            return lo + t * (hi - lo)

        elif self.dtype in ("categorical", "ordinal"):
            cats = self.domain
            if value in cats:
                idx = cats.index(value)
                step = (hi - lo) / max(len(cats), 1)
                return lo + (idx + 0.5) * step
            return lo

        elif self.dtype == "temporal":
            ts: float = semi(
                f"parse '{value}' as unix timestamp (float seconds)"
            )
            mn, mx = self.domain
            t = (ts - mn) / (mx - mn + 1e-9)
            return lo + t * (hi - lo)

        return lo

    def from_pixel(self, px: float) -> Any:
        """Map pixel position back to data value."""
        lo, hi = self.pixel_min, self.pixel_max
        if self.invert:
            lo, hi = hi, lo

        if self.dtype == "numeric":
            mn, mx = self.domain
            t = (px - lo) / (hi - lo + 1e-9)
            return mn + t * (mx - mn)

        elif self.dtype in ("categorical", "ordinal"):
            cats = self.domain
            step = (hi - lo) / max(len(cats), 1)
            idx = int((px - lo) / step)
            idx = max(0, min(idx, len(cats) - 1))
            return cats[idx]

        elif self.dtype == "temporal":
            mn, mx = self.domain
            t = (px - lo) / (hi - lo + 1e-9)
            ts = mn + t * (mx - mn)
            return semi(
                f"""format unix timestamp {ts} as a readable date string,
                matching the style of: {repr(self.values[0])}"""
            )

        return None

    def format_tick(self, value: Any) -> str:
        """Format a value for axis tick label."""
        if self.dtype == "numeric":
            return semi(
                f"""format number {value} for a chart axis tick.
                full range is {self.domain}.
                use K/M/B suffix for large numbers,
                appropriate decimal places for small ranges"""
            )
        elif self.dtype == "temporal":
            return semi(
                f"""format '{value}' as a short axis tick label.
                sample values: {repr(self.values[:5])}.
                use shortest unambiguous format"""
            )
        else:
            return str(value)

    def tick_values(self, n: int = 5) -> list:
        """Generate nice tick positions."""
        if self.dtype == "numeric":
            mn, mx = self.domain
            # Formal: always return n evenly-ish spaced ticks
            step = semi(
                f"""compute a 'nice' tick step for range [{mn}, {mx}]
                targeting approximately {n} ticks.
                return a round number (e.g. 1, 2, 5, 10, 25, 50, 100...)"""
            )
            ticks = []
            v = semi(f"round {mn} UP to nearest multiple of {step}")
            while v <= mx and len(ticks) < n + 2:
                ticks.append(v)
                v += step
            return ticks

        elif self.dtype in ("categorical", "ordinal"):
            return list(self.domain)

        elif self.dtype == "temporal":
            mn, mx = self.domain
            timestamps = semi(
                f"""generate {n} evenly spaced timestamps between
                {mn} and {mx} (unix seconds), snapped to nice
                boundaries (start of hour/day/month/year as appropriate)"""
            )
            return [
                semi(f"""format unix timestamp {ts} matching
                     style of {repr(self.values[0])}""")
                for ts in timestamps
            ]


# ── Chart types ──
# The formal code handles: SVG structure, mouse events, selection updates.
# semi() handles: type-aware scaling (via Scale), tick formatting, data prep.

@semiformal
class Chart:
    """Base chart with interactive brush. Renders in Jupyter."""

    def __init__(self, data: list[dict], width: int = 500, height: int = 350,
                 svg_id: str = None):
        self.data = data
        self.width = width
        self.height = height
        self.svg_id = svg_id or f"chart_{id(self)}"
        self.selection = Selection(data)
        self.mark = MarkConfig()
        self.brush_config = BrushConfig()
        self.padding = {"top": 20, "right": 20, "bottom": 50, "left": 60}

    @property
    def plot_width(self):
        return self.width - self.padding["left"] - self.padding["right"]

    @property
    def plot_height(self):
        return self.height - self.padding["top"] - self.padding["bottom"]

    def _render_axes_svg(self, x_scale: Scale, y_scale: Scale) -> str:
        p = self.padding
        lines = []

        # Formal: axis lines
        lines.append(
            f'<line x1="{p["left"]}" y1="{self.height - p["bottom"]}" '
            f'x2="{self.width - p["right"]}" y2="{self.height - p["bottom"]}" '
            f'stroke="#666" />'
        )
        lines.append(
            f'<line x1="{p["left"]}" y1="{p["top"]}" '
            f'x2="{p["left"]}" y2="{self.height - p["bottom"]}" '
            f'stroke="#666" />'
        )

        # Formal: tick marks (positions from Scale, formatting from Scale)
        for v in x_scale.tick_values():
            px = x_scale.to_pixel(v)
            label = x_scale.format_tick(v)
            lines.append(
                f'<text x="{px}" y="{self.height - p["bottom"] + 18}" '
                f'text-anchor="middle" font-size="10" fill="#666">'
                f'{html_lib.escape(str(label))}</text>'
            )

        for v in y_scale.tick_values():
            py = y_scale.to_pixel(v)
            label = y_scale.format_tick(v)
            lines.append(
                f'<text x="{p["left"] - 8}" y="{py + 4}" '
                f'text-anchor="end" font-size="10" fill="#666">'
                f'{html_lib.escape(str(label))}</text>'
            )

        return "\n    ".join(lines)

    def _brush_js(self, brush_type: str = "rect") -> str:
        """Generate JS for interactive brush.
        
        brush_type: 'rect' | 'x' | 'y'
        This is FORMAL code — the brush interaction mechanics
        don't change with data types. It's just mouse math.
        """
        m = self.mark
        bc = self.brush_config
        sid = self.svg_id

        # The brush JS is fully formal — no semi() needed
        # It's just coordinate tracking + rect drawing + hit testing
        if brush_type == "rect":
            hit_test = (
                "cx >= bx0 && cx <= bx1 && cy >= by0 && cy <= by1"
            )
            brush_update = """
                brush.setAttribute('x', bx0); brush.setAttribute('y', by0);
                brush.setAttribute('width', bx1-bx0);
                brush.setAttribute('height', by1-by0);"""
        elif brush_type == "x":
            hit_test = "cx >= bx0 && cx <= bx1"
            brush_update = f"""
                brush.setAttribute('x', bx0); brush.setAttribute('y', {self.padding['top']});
                brush.setAttribute('width', bx1-bx0);
                brush.setAttribute('height', {self.plot_height});"""
        elif brush_type == "y":
            hit_test = "cy >= by0 && cy <= by1"
            brush_update = f"""
                brush.setAttribute('x', {self.padding['left']});
                brush.setAttribute('y', by0);
                brush.setAttribute('width', {self.plot_width});
                brush.setAttribute('height', by1-by0);"""

        return f"""
<script>
(function() {{
  const svg = document.getElementById('{sid}');
  const brush = document.getElementById('{sid}_brush');
  const marks = svg.querySelectorAll('.mark');
  let x0, y0, brushing = false;

  function update(bx0, by0, bx1, by1) {{
    const sel = [];
    marks.forEach(m => {{
      const cx = parseFloat(m.dataset.px);
      const cy = parseFloat(m.dataset.py);
      const hit = {hit_test};
      m.setAttribute('fill', hit ? '{m.selected_fill}' : m.dataset.fill);
      m.setAttribute('stroke', hit ? '{m.selected_stroke}' : m.dataset.stroke);
      if (hit) sel.push(parseInt(m.dataset.idx));
    }});
    {brush_update}
    svg.dispatchEvent(new CustomEvent('brushed', {{detail: {{indices: sel}}}}));
  }}

  svg.addEventListener('mousedown', e => {{
    if (e.target.classList.contains('mark')) return;
    const r = svg.getBoundingClientRect();
    x0 = e.clientX - r.left; y0 = e.clientY - r.top;
    brushing = true;
  }});
  svg.addEventListener('mousemove', e => {{
    if (!brushing) return;
    const r = svg.getBoundingClientRect();
    const x1 = e.clientX - r.left, y1 = e.clientY - r.top;
    update(Math.min(x0,x1), Math.min(y0,y1), Math.max(x0,x1), Math.max(y0,y1));
  }});
  svg.addEventListener('mouseup', () => {{ brushing = false; }});
  svg.addEventListener('dblclick', () => {{
    brush.setAttribute('width', 0); brush.setAttribute('height', 0);
    marks.forEach(m => {{
      m.setAttribute('fill', m.dataset.fill);
      m.setAttribute('stroke', m.dataset.stroke);
    }});
    svg.dispatchEvent(new CustomEvent('brushed', {{detail: {{indices: []}}}}));
  }});
}})();
</script>"""


class Scatter(Chart):
    """Scatterplot with rectangular brush selection."""

    def __init__(self, data: list[dict], x: str, y: str, **kwargs):
        super().__init__(data, **kwargs)
        self.x_field = x
        self.y_field = y

        # Scale construction: formal structure, semi() handles type detection
        x_vals = [d[x] for d in data if x in d]
        y_vals = [d[y] for d in data if y in d]
        self.x_scale = Scale(x_vals, self.padding["left"],
                             self.width - self.padding["right"])
        self.y_scale = Scale(y_vals, self.padding["top"],
                             self.height - self.padding["bottom"],
                             invert=True)  # SVG y is flipped

    def to_html(self) -> str:
        m = self.mark
        bc = self.brush_config
        marks = []

        for i, d in enumerate(self.data):
            if self.x_field not in d or self.y_field not in d:
                continue
            cx = self.x_scale.to_pixel(d[self.x_field])
            cy = self.y_scale.to_pixel(d[self.y_field])
            marks.append(
                f'<circle class="mark" data-idx="{i}" '
                f'data-px="{cx:.1f}" data-py="{cy:.1f}" '
                f'data-fill="{m.fill}" data-stroke="{m.stroke}" '
                f'cx="{cx:.1f}" cy="{cy:.1f}" r="{m.size}" '
                f'fill="{m.fill}" stroke="{m.stroke}" '
                f'stroke-width="{m.stroke_width}" />'
            )

        axes = self._render_axes_svg(self.x_scale, self.y_scale)
        marks_str = "\n    ".join(marks)

        return f"""<svg id="{self.svg_id}" width="{self.width}"
     height="{self.height}"
     style="border:1px solid #ddd; user-select:none; font-family:sans-serif">
    {axes}
    {marks_str}
    <rect id="{self.svg_id}_brush" fill="{bc.fill}" stroke="{bc.stroke}"
          stroke-width="{bc.stroke_width}"
          x="0" y="0" width="0" height="0" pointer-events="none"/>
</svg>
{self._brush_js("rect")}"""

    def _repr_html_(self):
        return self.to_html()


class Line(Chart):
    """Line chart with x-interval brush selection."""

    def __init__(self, data: list[dict], x: str, y: str, **kwargs):
        super().__init__(data, **kwargs)
        self.x_field = x
        self.y_field = y

        x_vals = [d[x] for d in data if x in d]
        y_vals = [d[y] for d in data if y in d]
        self.x_scale = Scale(x_vals, self.padding["left"],
                             self.width - self.padding["right"])
        self.y_scale = Scale(y_vals, self.padding["top"],
                             self.height - self.padding["bottom"],
                             invert=True)

        # Formal: sort by x for line drawing
        self._sorted_indices = sorted(
            range(len(data)),
            key=lambda i: self.x_scale.to_pixel(data[i].get(x, 0))
        )

    def to_html(self) -> str:
        m = self.mark
        bc = self.brush_config

        # Formal: build polyline path + invisible hit targets
        path_points = []
        mark_elements = []

        for i in self._sorted_indices:
            d = self.data[i]
            if self.x_field not in d or self.y_field not in d:
                continue
            cx = self.x_scale.to_pixel(d[self.x_field])
            cy = self.y_scale.to_pixel(d[self.y_field])
            path_points.append(f"{cx:.1f},{cy:.1f}")

            # Small dot at each data point for selection hit testing
            mark_elements.append(
                f'<circle class="mark" data-idx="{i}" '
                f'data-px="{cx:.1f}" data-py="{cy:.1f}" '
                f'data-fill="{m.fill}" data-stroke="{m.stroke}" '
                f'cx="{cx:.1f}" cy="{cy:.1f}" r="3" '
                f'fill="{m.fill}" stroke="{m.stroke}" '
                f'stroke-width="{m.stroke_width}" />'
            )

        polyline = (f'<polyline points="{" ".join(path_points)}" '
                    f'fill="none" stroke="{m.fill}" stroke-width="2" />')
        axes = self._render_axes_svg(self.x_scale, self.y_scale)
        marks_str = "\n    ".join(mark_elements)

        return f"""<svg id="{self.svg_id}" width="{self.width}"
     height="{self.height}"
     style="border:1px solid #ddd; user-select:none; font-family:sans-serif">
    {axes}
    {polyline}
    {marks_str}
    <rect id="{self.svg_id}_brush" fill="{bc.fill}" stroke="{bc.stroke}"
          stroke-width="{bc.stroke_width}"
          x="0" y="0" width="0" height="0" pointer-events="none"/>
</svg>
{self._brush_js("x")}"""

    def _repr_html_(self):
        return self.to_html()


@semiformal
class GeoPlot(Chart):
    """Point map with rectangular brush. Renders lat/lon as projected points."""

    def __init__(self, data: list[dict], lat: str = "lat", lon: str = "lon",
                 label: str = None, **kwargs):
        super().__init__(data, **kwargs)
        self.lat_field = lat
        self.lon_field = lon
        self.label_field = label

        lats = [d[lat] for d in data if lat in d]
        lons = [d[lon] for d in data if lon in d]

        # Formal: lon → x, lat → y (with invert because lat increases upward)
        self.x_scale = Scale(lons, self.padding["left"],
                             self.width - self.padding["right"])
        self.y_scale = Scale(lats, self.padding["top"],
                             self.height - self.padding["bottom"],
                             invert=True)

    def to_html(self) -> str:
        m = self.mark
        bc = self.brush_config
        marks = []

        for i, d in enumerate(self.data):
            if self.lat_field not in d or self.lon_field not in d:
                continue
            cx = self.x_scale.to_pixel(d[self.lon_field])
            cy = self.y_scale.to_pixel(d[self.lat_field])
            marks.append(
                f'<circle class="mark" data-idx="{i}" '
                f'data-px="{cx:.1f}" data-py="{cy:.1f}" '
                f'data-fill="{m.fill}" data-stroke="{m.stroke}" '
                f'cx="{cx:.1f}" cy="{cy:.1f}" r="{m.size}" '
                f'fill="{m.fill}" stroke="{m.stroke}" '
                f'stroke-width="{m.stroke_width}" />'
            )
            if self.label_field and self.label_field in d:
                label = html_lib.escape(str(d[self.label_field]))
                marks.append(
                    f'<text x="{cx:.1f}" y="{cy - m.size - 3:.1f}" '
                    f'text-anchor="middle" font-size="9" fill="#333">'
                    f'{label}</text>'
                )

        # Semi: optional coastline/boundary context as background path
        lat_range = self.y_scale.domain
        lon_range = self.x_scale.domain
        background: str = semi(
            f"""generate a simplified SVG <path> element for land/coastline
            boundaries visible in lat range {lat_range},
            lon range {lon_range}, projected linearly to
            x:[{self.padding['left']}, {self.width - self.padding['right']}],
            y:[{self.height - self.padding['bottom']}, {self.padding['top']}]
            (y inverted). Use stroke="#ddd" fill="#f5f5f5".
            If the range is too large for meaningful coastlines,
            return an empty string."""
        )

        axes = self._render_axes_svg(self.x_scale, self.y_scale)
        marks_str = "\n    ".join(marks)

        return f"""<svg id="{self.svg_id}" width="{self.width}"
     height="{self.height}"
     style="border:1px solid #ddd; user-select:none; font-family:sans-serif">
    {background}
    {axes}
    {marks_str}
    <rect id="{self.svg_id}_brush" fill="{bc.fill}" stroke="{bc.stroke}"
          stroke-width="{bc.stroke_width}"
          x="0" y="0" width="0" height="0" pointer-events="none"/>
</svg>
{self._brush_js("rect")}"""

    def _repr_html_(self):
        return self.to_html()