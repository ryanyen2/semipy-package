from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

from semipy import semiformal, semi
from semipy.agents.config import configure


class SmartChart:
    def __init__(self, data: dict[str, list[float]], title: str):
        self.data = data
        self.title = title
        self._fig: plt.Figure | None = None

    @semiformal
    def infer_layout(self) -> tuple[int, int]:
        n = len(self.data)

        #> Given {n} variables, decide subplot grid (rows, cols).
        return rows, cols

    @semiformal
    def infer_axis_config(self, key: str, values: list[float]) -> dict[str, Any]:
        assert values is not None
        assert len(values) == len(self.data[key])

        #> Infer axis display config for variable named "{key}" with sample values.
        #> Decide scale, label, and tick density
        return {"scale": scale, "label": label, "tick_density": tick_density}

    @semiformal
    def render(self, fig_size: tuple[float, float] | None = None) -> plt.Figure:
        rows, cols = self.infer_layout()
        fig, axes = plt.subplots(rows, cols, figsize=fig_size or (cols * 4, rows * 3))
        axes_flat = [axes] if rows * cols == 1 else list(axes.flat)
        #> infer color palette based on the domain of the data

        for ax, (key, values) in zip(axes_flat, self.data.items()):
            config = self.infer_axis_config(key, values)
            ax.set_yscale(config["scale"])
            ax.set_ylabel(config["label"])
            ax.plot(range(len(values)), values)
            ax.set_title(key)

            tick_fmt: ticker.Formatter = semi(f"tick formatter object for '{key}' with scale={config['scale']}, density={config['tick_density']}, range=[{min(values):.3g}, {max(values):.3g}]. Return a matplotlib.ticker.FuncFormatter axis-independent (do not use ScalarFormatter internals that require an axis). Format numeric ticks into human-friendly strings (respect unit suffix inferred from key like _ppm, _K, _ms). Density controls number of decimals; for 'dense' use more precision, for 'sparse' use fewer decimals.", expected_type=ticker.Formatter)
            ax.yaxis.set_major_formatter(tick_fmt)

            x_label: str = semi(f"x-axis label for a time-series plot of '{key}' with {len(values)} points", expected_type=str)
            ax.set_xlabel(x_label)

        fig.suptitle(self.title)
        fig.tight_layout()
        self._fig = fig
        return fig


def main() -> None:
    configure(
        cache_dir=Path(".semiformal_visual_builder"),
        verbose=True,
        enable_execution_test=True,
        max_retries=2,
    )

    data = {
        "co2_ppm": [280, 285, 300, 330, 360],
        "temperature_K": [287, 288, 289.5, 291, 293],
        "latency_ms": [0.3, 0.5, 0.4, 0.7, 1.2],
        "pressure_Pa": [101325, 101300, 101280, 101250, 101220],
    }
    chart = SmartChart(data=data, title="Inferred Layout + Axis Config")

    fig = chart.render(fig_size=(14, 10))
    out_dir = Path("examples/output")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "smart_chart_demo.png"
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print("Saved:", out_path)


if __name__ == "__main__":
    main()

