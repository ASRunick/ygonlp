"""Generate stacked yearly release-count figures from release-count analysis output."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CATEGORIES = ("Main Monster", "Extra Deck", "Spell", "Trap", "Pendulum", "Ritual", "Other")
CATEGORY_COLORS = {
    "Main Monster": "#4c78a8",
    "Extra Deck": "#f58518",
    "Spell": "#54a24b",
    "Trap": "#e45756",
    "Pendulum": "#b279a2",
    "Ritual": "#72b7b2",
    "Other": "#8b8b8b",
}
EXTRA_DECK_TYPES = frozenset({"Fusion Monster", "Synchro Monster", "XYZ Monster", "Link Monster"})
FIGURE_DPI = 200
OUTPUT_BASENAME = "release-counts-by-category"


def _load_json_from_metadata(metadata_path: Path) -> dict[str, Any]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("completed") is not True:
        raise ValueError(f"incomplete release-count metadata: {metadata_path}")
    filename = metadata.get("json_output_file")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError(f"invalid json_output_file in {metadata_path}")
    data_path = metadata_path.parent / filename
    raw = data_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata.get("json_output_checksum"):
        raise ValueError(f"checksum mismatch for {data_path}")
    return json.loads(raw.decode("utf-8"))


def category_for(card_type: str) -> str:
    if "Pendulum" in card_type:
        return "Pendulum"
    if "Ritual" in card_type:
        return "Ritual"
    if card_type in EXTRA_DECK_TYPES:
        return "Extra Deck"
    if card_type == "Spell Card":
        return "Spell"
    if card_type == "Trap Card":
        return "Trap"
    if card_type.endswith("Monster"):
        return "Main Monster"
    return "Other"


def aggregate_categories(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        totals[row["year"]][category_for(row["card_type"])] += row["release_count"]
    return {year: {category: totals[year][category] for category in CATEGORIES} for year in sorted(totals)}


def _configure_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "axes.titleweight": "bold",
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
        "grid.alpha": 0.25, "grid.linewidth": 0.7, "svg.fonttype": "none",
        "svg.hashsalt": "ygonlp-release-count-figures-v1",
    })


def _normalize_svg(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(line.rstrip() for line in lines) + "\n", encoding="utf-8", newline="\n")


def plot_release_counts(data: dict[str, Any], output: Path) -> None:
    rows = data.get("by_year_card_type")
    if not isinstance(rows, list) or not rows:
        raise ValueError("release count JSON has no yearly card-type rows")
    totals = aggregate_categories(rows)
    years = [int(year) for year in totals]
    figure, axis = plt.subplots(figsize=(11, 5.5))
    bottom = [0] * len(years)
    for category in CATEGORIES:
        values = [totals[str(year)][category] for year in years]
        axis.bar(years, values, bottom=bottom, label=category, color=CATEGORY_COLORS[category], width=0.75)
        bottom = [current + value for current, value in zip(bottom, values, strict=True)]
    axis.set_title("Yearly releases by card-type category")
    axis.set_xlabel("TCG candidate first-appearance year")
    axis.set_ylabel("Release count")
    axis.set_xticks(years[::2])
    axis.tick_params(axis="x", rotation=45)
    axis.set_ylim(bottom=0)
    axis.legend(title="Card category", ncol=2)
    output.mkdir(parents=True, exist_ok=True)
    for suffix in ("svg", "png"):
        path = output / f"{OUTPUT_BASENAME}.{suffix}"
        figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight", facecolor="white",
                       metadata={"Date": None} if suffix == "svg" else None)
        if suffix == "svg":
            _normalize_svg(path)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-metadata", type=Path, required=True, help="release-counts metadata JSON")
    parser.add_argument("--output", type=Path, required=True, help="output directory")
    args = parser.parse_args()

    _configure_style()
    plot_release_counts(_load_json_from_metadata(args.input_metadata), args.output)
    print(f"generated: {OUTPUT_BASENAME}.svg, {OUTPUT_BASENAME}.png")


if __name__ == "__main__":
    main()
