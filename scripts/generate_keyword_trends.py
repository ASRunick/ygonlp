"""Generate mechanic keyword trend data and figures from preprocessing output."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ygonlp.keyword_trends import build_keyword_trends
from ygonlp.preprocess import verify_preprocessed_cache

CSV_FIELDS = ("keyword", "year", "occurrence_count", "document_count", "document_ratio")
KEYWORDS = {
    "graveyard": [r"\bgraveyard\b"],
    "gy": [r"\bGY\b"],
    "banish": [r"\bbanish(?:ed|ing)?\b"],
    "search": [r"\b(?:add|search)\b"],
}
FIGURE_DPI = 200


def load_records(metadata_path: Path) -> list[dict[str, Any]]:
    """Load records after validating the preprocessing metadata and JSONL checksum."""
    verified = verify_preprocessed_cache(metadata_path)
    return [json.loads(line) for line in verified["data_path"].read_text(encoding="utf-8").splitlines()]


def write_outputs(result: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "keyword-trends.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n",
    )
    with (output / "keyword-trends.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row[field] for field in CSV_FIELDS} for row in result["trends"])


def _configure_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "axes.titleweight": "bold",
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
        "grid.alpha": 0.25, "grid.linewidth": 0.7, "svg.fonttype": "none",
        "svg.hashsalt": "ygonlp-keyword-trends-v1",
    })


def _normalize_svg(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(line.rstrip() for line in lines) + "\n", encoding="utf-8", newline="\n")


def plot_keyword_trends(result: dict[str, Any], output: Path) -> None:
    rows = result["trends"]
    if not rows:
        raise ValueError("keyword trend analysis has no yearly rows")
    figure, axis = plt.subplots(figsize=(10, 5))
    for keyword, color in zip(sorted({row["keyword"] for row in rows}), plt.rcParams["axes.prop_cycle"].by_key()["color"], strict=True):
        keyword_rows = [row for row in rows if row["keyword"] == keyword]
        axis.plot([int(row["year"]) for row in keyword_rows], [row["document_ratio"] for row in keyword_rows],
                  label=keyword, color=color, linewidth=2.2, marker="o", markersize=4)
    years = sorted({int(row["year"]) for row in rows})
    axis.set_title("Mechanic keyword document ratio by TCG candidate first-appearance year")
    axis.set_xlabel("TCG candidate first-appearance year")
    axis.set_ylabel("Document ratio")
    axis.set_xticks(years[::2])
    axis.tick_params(axis="x", rotation=45)
    axis.set_ylim(0, 1)
    axis.legend(title="Keyword")
    for suffix in ("svg", "png"):
        path = output / f"keyword-trends.{suffix}"
        figure.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight", facecolor="white",
                       metadata={"Date": None} if suffix == "svg" else None)
        if suffix == "svg":
            _normalize_svg(path)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-metadata", type=Path, required=True, help="preprocessing metadata JSON")
    parser.add_argument("--output", type=Path, required=True, help="output directory")
    args = parser.parse_args()

    result = build_keyword_trends(load_records(args.input_metadata), KEYWORDS, datetime.now(timezone.utc).date())
    _configure_style()
    write_outputs(result, args.output)
    plot_keyword_trends(result, args.output)
    print("generated: keyword-trends.json, keyword-trends.csv, keyword-trends.svg, keyword-trends.png")


if __name__ == "__main__":
    main()
