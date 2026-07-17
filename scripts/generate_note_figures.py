"""Generate reproducible SVG and PNG figures for the YGONLP overview note."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


FIGURE_DPI = 200
OUTPUT_BASENAMES = (
    "text-length-character-count-trend",
    "yearly-release-count",
    "analysis-pipeline-overview",
)


def _load_json_from_metadata(metadata_path: Path, output_field: str) -> dict[str, Any]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("completed") is not True:
        raise ValueError(f"incomplete analysis metadata: {metadata_path}")
    filename = metadata.get(output_field)
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError(f"invalid {output_field} in {metadata_path}")
    data_path = metadata_path.parent / filename
    raw = data_path.read_bytes()
    checksum_field = output_field.replace("_file", "_checksum")
    if hashlib.sha256(raw).hexdigest() != metadata.get(checksum_field):
        raise ValueError(f"checksum mismatch for {data_path}")
    return json.loads(raw.decode("utf-8"))


def _configure_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "axes.titleweight": "bold",
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
        "grid.alpha": 0.25, "grid.linewidth": 0.7, "svg.fonttype": "none",
        "svg.hashsalt": "ygonlp-note-figures-v1",
    })


def _normalize_svg(path: Path) -> None:
    """Remove trailing whitespace emitted by Matplotlib without changing XML structure."""
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(line.rstrip() for line in lines) + "\n", encoding="utf-8", newline="\n")


def _save(figure: plt.Figure, output: Path, basename: str) -> None:
    for suffix in ("svg", "png"):
        path = output / f"{basename}.{suffix}"
        metadata = {"Date": None} if suffix == "svg" else None
        figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight", facecolor="white", metadata=metadata)
        if suffix == "svg":
            _normalize_svg(path)
    plt.close(figure)


def plot_text_length_trend(data: dict[str, Any], output: Path) -> None:
    groups = data.get("by_tcg_year")
    if not isinstance(groups, list) or not groups:
        raise ValueError("timeseries JSON has no yearly groups")
    years = [int(group["year"]) for group in groups]
    means = [group["metrics"]["character_count"]["mean"] for group in groups]
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.plot(years, means, color="#2463a5", linewidth=2.4, marker="o", markersize=4)
    axis.set_title("Average effect-text length by TCG candidate first-appearance year")
    axis.set_xlabel("TCG candidate first-appearance year")
    axis.set_ylabel("Average character count")
    axis.set_xticks(years[::2])
    axis.tick_params(axis="x", rotation=45)
    axis.set_ylim(bottom=0)
    _save(figure, output, "text-length-character-count-trend")


def plot_yearly_release_count(data: dict[str, Any], output: Path) -> None:
    rows = data.get("overall")
    if not isinstance(rows, list) or not rows:
        raise ValueError("release count JSON has no overall yearly rows")
    years = [int(row["year"]) for row in rows]
    counts = [row["release_count"] for row in rows]
    partial = [row["is_partial_year"] for row in rows]
    colors = ["#8b95a1" if is_partial else "#3a7d44" for is_partial in partial]
    figure, axis = plt.subplots(figsize=(10, 5))
    bars = axis.bar(years, counts, color=colors, width=0.75)
    for bar, is_partial in zip(bars, partial, strict=True):
        if is_partial:
            bar.set_hatch("//")
    axis.set_title("Yearly cards by TCG candidate first-appearance year")
    axis.set_xlabel("TCG candidate first-appearance year")
    axis.set_ylabel("Yearly release count")
    axis.set_xticks(years[::2])
    axis.tick_params(axis="x", rotation=45)
    axis.set_ylim(bottom=0)
    if any(partial):
        axis.text(0.99, 0.97, "Hatched bar: partial cutoff year", transform=axis.transAxes,
                  ha="right", va="top", fontsize=9, color="#4a5560")
    _save(figure, output, "yearly-release-count")


def _box(axis: plt.Axes, x: float, y: float, label: str, *, width: float = 0.18, fontsize: int = 10) -> None:
    box = FancyBboxPatch((x - width / 2, y - 0.07), width, 0.14, boxstyle="round,pad=0.012",
                         linewidth=1.2, edgecolor="#385170", facecolor="#eaf2f8")
    axis.add_patch(box)
    axis.text(x, y, label, ha="center", va="center", fontsize=fontsize, color="#172b3a")


def _arrow(axis: plt.Axes, start: tuple[float, float], end: tuple[float, float]) -> None:
    axis.add_patch(FancyArrowPatch(start, end, arrowstyle="->", mutation_scale=14,
                                   linewidth=1.3, color="#385170"))


def plot_pipeline_overview(output: Path) -> None:
    figure, axis = plt.subplots(figsize=(11, 5.5))
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    stages = [(0.12, "collect"), (0.36, "preprocess"), (0.60, "measure"), (0.84, "summarize")]
    for x, label in stages:
        _box(axis, x, 0.80, label, width=0.18)
    for (x1, _), (x2, _) in zip(stages, stages[1:]):
        _arrow(axis, (x1 + 0.10, 0.80), (x2 - 0.10, 0.80))
    _box(axis, 0.12, 0.57, "snapshot-prices", width=0.20, fontsize=9)
    _arrow(axis, (0.12, 0.73), (0.12, 0.65))
    commands = [
        (0.15, 0.34, "search-similar"), (0.40, 0.34, "analyze-vocabulary"),
        (0.15, 0.14, "analyze-topics"), (0.65, 0.34, "analyze-timeseries"),
        (0.89, 0.34, "analyze-releases"), (0.72, 0.14, "analyze-prices"),
    ]
    for x, y, label in commands:
        _box(axis, x, y, label, width=0.20, fontsize=8)
    for x, y in ((0.15, 0.34), (0.40, 0.34), (0.15, 0.14)):
        _arrow(axis, (0.36, 0.73), (x, y + 0.08))
    for x, y in ((0.65, 0.34), (0.89, 0.34)):
        _arrow(axis, (0.60, 0.73), (x, y + 0.08))
    _arrow(axis, (0.60, 0.73), (0.72, 0.22))
    _arrow(axis, (0.22, 0.57), (0.61, 0.19))
    axis.set_title("YGONLP analysis pipeline", loc="left", pad=12)
    _save(figure, output, "analysis-pipeline-overview")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeseries-metadata", type=Path, required=True)
    parser.add_argument("--release-counts-metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("docs/note/assets"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    _configure_style()
    plot_text_length_trend(_load_json_from_metadata(args.timeseries_metadata, "json_output_file"), args.output)
    plot_yearly_release_count(_load_json_from_metadata(args.release_counts_metadata, "json_output_file"), args.output)
    plot_pipeline_overview(args.output)
    print("generated: " + ", ".join(f"{name}.svg/.png" for name in OUTPUT_BASENAMES))


if __name__ == "__main__":
    main()
