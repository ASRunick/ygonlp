"""TCG初出候補日の年別カードrelease countを決定論的に集計・保存する。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .summarize import (
    OUTPUT_FORMAT_ORDER,
    OUTPUT_SUFFIXES,
    PROJECT_VERSION,
    SummarizeError,
    _best_effort_unlink,
    _read_json,
    _safe_child,
    _write_atomic,
    load_source,
)

RELEASE_COUNTS_METADATA_SCHEMA_VERSION = 1
RELEASE_COUNTS_JSON_SCHEMA_VERSION = 1
RELEASE_COUNTS_IDENTIFIER = "tcg_first_appearance_candidate_yearly_release_counts_v1"
DATE_DEFINITION = "tcg_date_adopted_tcg_set_source_first_release_candidate_date_v1"
CURRENT_DATE_CUTOFF_POLICY = "tcg_date_after_utc_current_date_excluded_v1"
ZERO_YEAR_POLICY = "included_year_through_cutoff_year_zero_filled_v1"
PARTIAL_YEAR_POLICY = "cutoff_year_partial_unless_cutoff_is_december_31_v1"
OUTPUT_ORDER = "overall_year_ascending_then_year_card_type_year_ascending_card_type_ascending_v1"
CSV_FIELDS = ("scope", "year", "card_type", "is_partial_year", "release_count", "cumulative_release_count")
AtomicWriter = Callable[[Path, bytes], None]


class ReleaseCountsError(RuntimeError):
    """release count分析入力または保存のFatal error。"""


def _is_partial_year(year: int, cutoff: date) -> bool:
    return year == cutoff.year and (cutoff.month, cutoff.day) != (12, 31)


def _row(scope: str, year: int, card_type: str | None, count: int, cumulative: int, cutoff: date) -> dict[str, Any]:
    return {
        "scope": scope,
        "year": str(year),
        "card_type": card_type,
        "is_partial_year": _is_partial_year(year, cutoff),
        "release_count": count,
        "cumulative_release_count": cumulative,
    }


def build_release_counts(records: Iterable[dict[str, Any]], cutoff: date) -> dict[str, Any]:
    """tcg_dateだけを用い、最初のincluded yearからcutoff yearまで集計する。"""
    yearly: Counter[int] = Counter()
    yearly_type: Counter[tuple[int, str]] = Counter()
    missing = future = 0
    for record in records:
        value = record["tcg_date"]
        if value is None:
            missing += 1
        elif value > cutoff.isoformat():
            future += 1
        else:
            year = int(value[:4])
            yearly[year] += 1
            yearly_type[(year, record["card_type"])] += 1

    years = range(min(yearly), cutoff.year + 1) if yearly else range(0)
    overall: list[dict[str, Any]] = []
    cumulative = 0
    for year in years:
        count = yearly[year]
        cumulative += count
        overall.append(_row("overall", year, None, count, cumulative, cutoff))

    by_year_card_type: list[dict[str, Any]] = []
    for card_type in sorted({card_type for _, card_type in yearly_type}):
        cumulative = 0
        for year in years:
            count = yearly_type[(year, card_type)]
            cumulative += count
            by_year_card_type.append(_row("year_card_type", year, card_type, count, cumulative, cutoff))
    by_year_card_type.sort(key=lambda row: (row["year"], row["card_type"]))
    return {
        "schema_version": RELEASE_COUNTS_JSON_SCHEMA_VERSION,
        "release_counts_identifier": RELEASE_COUNTS_IDENTIFIER,
        "date_definition": DATE_DEFINITION,
        "current_date_cutoff": cutoff.isoformat(),
        "missing_date_count": missing,
        "future_date_count": future,
        "included_record_count": sum(yearly.values()),
        "zero_year_policy": ZERO_YEAR_POLICY,
        "partial_year_policy": PARTIAL_YEAR_POLICY,
        "overall": overall,
        "by_year_card_type": by_year_card_type,
    }


def release_counts_cache_key(source: Any, cutoff: date) -> str:
    payload = {
        "metadata_schema_version": RELEASE_COUNTS_METADATA_SCHEMA_VERSION,
        "json_schema_version": RELEASE_COUNTS_JSON_SCHEMA_VERSION,
        "source_measurement_cache_key": source.metadata["measurement_cache_key"],
        "source_measurement_checksum": source.metadata["output_checksum"],
        "date_definition": DATE_DEFINITION,
        "current_date_cutoff": cutoff.isoformat(),
        "current_date_cutoff_policy": CURRENT_DATE_CUTOFF_POLICY,
        "zero_year_policy": ZERO_YEAR_POLICY,
        "partial_year_policy": PARTIAL_YEAR_POLICY,
        "output_order": OUTPUT_ORDER,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [*result["overall"], *result["by_year_card_type"]]


def _value(value: Any) -> str | int | bool:
    return "" if value is None else value


def serialize_json(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def serialize_csv(result: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in _rows(result):
        writer.writerow({field: _value(row[field]) for field in CSV_FIELDS})
    return output.getvalue().encode("utf-8")


def serialize_markdown(result: dict[str, Any]) -> bytes:
    lines = ["| " + " | ".join(CSV_FIELDS) + " |", "|" + "|".join("---" for _ in CSV_FIELDS) + "|"]
    for row in _rows(result):
        lines.append("| " + " | ".join(str(_value(row[field])).replace("|", "\\|") for field in CSV_FIELDS) + " |")
    return ("\n".join(lines) + "\n").encode("utf-8")


def output_metadata_path(output: Path, key: str) -> Path:
    return output / f"release-counts-{key[:16]}.metadata.json"


def output_data_path(output: Path, key: str, checksum: str, suffix: str) -> Path:
    return output / f"release-counts-{key[:16]}-{checksum[:16]}.{suffix}"


def _valid_output(output: Path, key: str, source: Any, cutoff: date) -> bool:
    try:
        metadata = _read_json(output_metadata_path(output, key))
        expected = {
            "metadata_schema_version": RELEASE_COUNTS_METADATA_SCHEMA_VERSION, "completed": True,
            "release_counts_cache_key": key, "release_counts_identifier": RELEASE_COUNTS_IDENTIFIER,
            "source_measurement_cache_key": source.metadata["measurement_cache_key"],
            "source_measurement_checksum": source.metadata["output_checksum"],
            "date_definition": DATE_DEFINITION, "current_date_cutoff": cutoff.isoformat(),
            "current_date_cutoff_policy": CURRENT_DATE_CUTOFF_POLICY, "zero_year_policy": ZERO_YEAR_POLICY,
            "partial_year_policy": PARTIAL_YEAR_POLICY, "output_ordering_identifier": OUTPUT_ORDER,
        }
        if not isinstance(metadata, dict) or any(metadata.get(name) != value for name, value in expected.items()):
            return False
        for name in OUTPUT_FORMAT_ORDER:
            checksum, size, filename = (metadata.get(f"{name}_output_checksum"), metadata.get(f"{name}_output_file_size"), metadata.get(f"{name}_output_file"))
            path = _safe_child(output, filename)
            if not isinstance(checksum, str) or not isinstance(size, int) or size < 0 or filename != output_data_path(output, key, checksum, OUTPUT_SUFFIXES[name]).name or path is None or not path.is_file():
                return False
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum:
                return False
        return True
    except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return False


def analyze_release_counts(input_metadata: Path, output: Path, *, force: bool = False, dry_run: bool = False, today: date | None = None, writer: AtomicWriter = _write_atomic) -> dict[str, Any]:
    try:
        source = load_source(input_metadata)
    except SummarizeError as exc:
        raise ReleaseCountsError(str(exc)) from exc
    cutoff = today or datetime.now(timezone.utc).date()
    key = release_counts_cache_key(source, cutoff)
    metadata_path = output_metadata_path(output, key)
    hit = _valid_output(output, key, source, cutoff)
    if hit and not force and not dry_run:
        metadata = _read_json(metadata_path)
        return {"status": "cache_hit", "cache_hit": True, "release_counts_cache_key": key, "source": source, "output_metadata_path": metadata_path, "output_paths": {name: output / metadata[f"{name}_output_file"] for name in OUTPUT_FORMAT_ORDER}}
    result = build_release_counts(source.records, cutoff)
    plan = {"status": "planned", "cache_hit": hit, "release_counts_cache_key": key, "source": source, "result": result, "output_metadata_path": metadata_path}
    if dry_run:
        return plan
    contents = {"json": serialize_json(result), "csv": serialize_csv(result), "markdown": serialize_markdown(result)}
    paths = {name: output_data_path(output, key, hashlib.sha256(contents[name]).hexdigest(), OUTPUT_SUFFIXES[name]) for name in OUTPUT_FORMAT_ORDER}
    created: list[Path] = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_FORMAT_ORDER:
            if paths[name].exists():
                if not paths[name].is_file() or paths[name].read_bytes() != contents[name]:
                    raise OSError("同名のrelease count generationが期待する内容と一致しません")
            else:
                writer(paths[name], contents[name]); created.append(paths[name])
        metadata = {
            "metadata_schema_version": RELEASE_COUNTS_METADATA_SCHEMA_VERSION, "completed": True,
            "release_counts_cache_key": key, "release_counts_identifier": RELEASE_COUNTS_IDENTIFIER,
            "source_measurement_metadata_file": source.metadata_path.name, "source_measurement_data_file": source.data_path.name,
            "source_measurement_cache_key": source.metadata["measurement_cache_key"], "source_measurement_checksum": source.metadata["output_checksum"],
            "date_definition": DATE_DEFINITION, "current_date_cutoff": cutoff.isoformat(), "current_date_cutoff_policy": CURRENT_DATE_CUTOFF_POLICY,
            "zero_year_policy": ZERO_YEAR_POLICY, "partial_year_policy": PARTIAL_YEAR_POLICY,
            "missing_date_count": result["missing_date_count"], "future_date_count": result["future_date_count"], "included_record_count": result["included_record_count"],
            "output_ordering_identifier": OUTPUT_ORDER, "output_formats": list(OUTPUT_FORMAT_ORDER), "project_version": PROJECT_VERSION,
        }
        for name in OUTPUT_FORMAT_ORDER:
            metadata.update({f"{name}_output_file": paths[name].name, f"{name}_output_checksum": hashlib.sha256(contents[name]).hexdigest(), f"{name}_output_file_size": len(contents[name])})
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        for path in created:
            _best_effort_unlink(path)
        raise ReleaseCountsError("release count分析出力の保存に失敗しました。既存出力は変更していません") from exc
    return {**plan, "status": "analyzed", "output_paths": paths}


def dry_run_lines(input_metadata: Path, output: Path, *, force: bool = False) -> list[str]:
    plan = analyze_release_counts(input_metadata, output, force=force, dry_run=True)
    result = plan["result"]
    return [
        f"input metadata path: {input_metadata}", f"included record count: {result['included_record_count']}",
        f"missing date count: {result['missing_date_count']}", f"future date count: {result['future_date_count']}",
        f"current date cutoff: {result['current_date_cutoff']}", f"output directory: {output}",
        f"release counts cache key: {plan['release_counts_cache_key']}", f"valid existing output: {'yes' if plan['cache_hit'] else 'no'}",
        f"--force: {'yes' if force else 'no'}", f"release count analysis required: {'yes' if force or not plan['cache_hit'] else 'no'}",
    ]
