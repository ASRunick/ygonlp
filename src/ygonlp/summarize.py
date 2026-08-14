"""測定済みテキスト指標を決定論的な記述統計へ集計・出力する。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from .artifacts import best_effort_unlink as _best_effort_unlink
from .artifacts import read_json as _read_json
from .artifacts import safe_child as _safe_child
from .artifacts import write_bytes_atomic as _write_atomic
from .measure import (
    CHARACTER_METRIC_IDENTIFIER,
    CHARACTER_METRIC_VERSION,
    MEASUREMENT_METADATA_SCHEMA_VERSION,
    MEASUREMENT_RECORD_SCHEMA_VERSION,
    RECORD_FIELDS as MEASUREMENT_RECORD_FIELDS,
    SENTENCE_METRIC_IDENTIFIER,
    SENTENCE_METRIC_VERSION,
    SORT_ORDER as MEASUREMENT_SORT_ORDER,
    WORD_METRIC_IDENTIFIER,
    WORD_METRIC_VERSION,
)

SUMMARY_METADATA_SCHEMA_VERSION = 1
SUMMARY_JSON_SCHEMA_VERSION = 1
SUMMARY_CSV_SCHEMA_VERSION = 1
SUMMARY_MARKDOWN_SCHEMA_VERSION = 1
SUMMARY_IDENTIFIER = "text_metric_descriptive_summary_v1"
GROUPING_IDENTIFIER = "overall_and_tcg_year_unknown_last_v1"
STATISTIC_IDENTIFIER = "numpy_mean_median_std_ddof0_percentile_linear_v1"
PERCENTILE_METHOD = "linear"
STANDARD_DEVIATION_DDOF = 0
FLOAT_PRECISION = 6
OUTPUT_FORMAT_ORDER = ("json", "csv", "markdown")
OUTPUT_FORMATS = OUTPUT_FORMAT_ORDER
OUTPUT_SUFFIXES = {"json": "json", "csv": "csv", "markdown": "md"}
JSON_FORMAT_IDENTIFIER = "canonical_summary_json_v1"
CSV_FORMAT_IDENTIFIER = "summary_long_format_csv_v1"
MARKDOWN_FORMAT_IDENTIFIER = "summary_long_format_markdown_v1"
OUTPUT_ORDER = "overall_then_tcg_year_ascending_then_unknown_last_metric_order"
UNKNOWN_GROUP = "unknown"
UNKNOWN_GROUP_POLICY = "tcg_date_null_as_unknown_last_v1"
PROJECT_VERSION = "0.0.0"
KEY_PREFIX_LENGTH = 16
CONTENT_PREFIX_LENGTH = 16
METRICS = ("character_count", "word_count", "sentence_count")
STATISTIC_FIELDS = (
    "count", "mean", "median", "minimum", "maximum", "population_standard_deviation", "q1", "q3",
)
CSV_FIELDS = ("scope", "group", "metric", *STATISTIC_FIELDS)


class SummarizeError(RuntimeError):
    """集計入力または保存のFatal error。"""


AtomicWriter = Callable[[Path, bytes], None]


@dataclass(frozen=True)
class Source:
    metadata_path: Path
    data_path: Path
    metadata: dict[str, Any]
    records: list[dict[str, Any]]


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_non_negative_int(value: Any) -> bool:
    return _is_int(value) and value >= 0


def _valid_date(value: str) -> bool:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def _validate_record(record: Any, previous_card_id: int | None) -> int:
    if not isinstance(record, dict) or tuple(record) != MEASUREMENT_RECORD_FIELDS:
        raise SummarizeError("measurement JSONLのrecord schemaが不正です")
    if (
        not _is_int(record.get("schema_version"))
        or record["schema_version"] != MEASUREMENT_RECORD_SCHEMA_VERSION
        or not _is_int(record.get("card_id"))
    ):
        raise SummarizeError("measurement JSONLのschema versionまたはcard_idが不正です")
    card_id = record["card_id"]
    if previous_card_id is not None and card_id <= previous_card_id:
        raise SummarizeError("measurement JSONLのcard_id順序または一意性が不正です")
    if any(not isinstance(record.get(field), str) for field in ("name", "card_type", "frame_type", "text_normalized")):
        raise SummarizeError("measurement JSONLの必須string fieldが不正です")
    if record.get("tcg_date") is not None:
        if not isinstance(record["tcg_date"], str) or not _valid_date(record["tcg_date"]):
            raise SummarizeError("measurement JSONLのtcg_dateが不正です")
    if any(not _is_non_negative_int(record.get(field)) for field in METRICS):
        raise SummarizeError("measurement JSONLのmetric countが不正です")
    return card_id


def load_source(metadata_path: Path) -> Source:
    """measurement metadataを信頼境界として全JSONLを検証・解決する。"""
    try:
        metadata = _read_json(metadata_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SummarizeError("measurement metadataを読み込めません") from exc
    if not isinstance(metadata, dict):
        raise SummarizeError("measurement metadataはobjectである必要があります")
    required = {
        "metadata_schema_version": MEASUREMENT_METADATA_SCHEMA_VERSION,
        "completed": True,
        "measurement_record_schema_version": MEASUREMENT_RECORD_SCHEMA_VERSION,
        "character_metric_version": CHARACTER_METRIC_VERSION,
        "word_metric_version": WORD_METRIC_VERSION,
        "sentence_metric_version": SENTENCE_METRIC_VERSION,
        "character_metric_identifier": CHARACTER_METRIC_IDENTIFIER,
        "word_metric_identifier": WORD_METRIC_IDENTIFIER,
        "sentence_metric_identifier": SENTENCE_METRIC_IDENTIFIER,
        "sort_order": MEASUREMENT_SORT_ORDER,
    }
    integer_definition_fields = (
        "metadata_schema_version", "measurement_record_schema_version", "character_metric_version",
        "word_metric_version", "sentence_metric_version",
    )
    if (
        metadata.get("completed") is not True
        or any(not _is_int(metadata.get(field)) for field in integer_definition_fields)
        or any(metadata.get(field) != value for field, value in required.items())
    ):
        raise SummarizeError("measurement metadataのschema、metric定義、またはsort orderが対応していません")
    for field in ("measurement_cache_key", "output_checksum", "source_preprocessing_cache_key", "source_preprocessing_checksum"):
        if not isinstance(metadata.get(field), str) or not metadata[field]:
            raise SummarizeError(f"measurement metadataの{field}が不正です")
    for field in ("measured_record_count", "output_file_size", "input_record_count"):
        if not _is_non_negative_int(metadata.get(field)):
            raise SummarizeError(f"measurement metadataの{field}が不正です")
    data_path = _safe_child(metadata_path.parent, metadata.get("output_data_file"))
    if data_path is None or not data_path.is_file():
        raise SummarizeError("measurement metadataが指すJSONL fileが不正です")
    try:
        raw = data_path.read_bytes()
    except OSError as exc:
        raise SummarizeError("measurement JSONLを読み込めません") from exc
    if len(raw) != metadata["output_file_size"] or hashlib.sha256(raw).hexdigest() != metadata["output_checksum"]:
        raise SummarizeError("measurement JSONLのsizeまたはchecksumがmetadataと一致しません")
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
        raise SummarizeError("measurement JSONLのエンコーディングまたは改行が不正です")
    if not raw:
        records: list[dict[str, Any]] = []
    else:
        if not raw.endswith(b"\n"):
            raise SummarizeError("measurement JSONLに最終LFがありません")
        try:
            records = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SummarizeError("measurement JSONLをparseできません") from exc
    if len(records) != metadata["measured_record_count"]:
        raise SummarizeError("measurement JSONLのrecord countがmetadataと一致しません")
    previous_card_id: int | None = None
    for record in records:
        previous_card_id = _validate_record(record, previous_card_id)
    return Source(metadata_path=metadata_path, data_path=data_path, metadata=metadata, records=records)


def _rounded(value: Any) -> float:
    result = round(float(value), FLOAT_PRECISION)
    return 0.0 if result == 0 else result


def metric_statistics(values: Iterable[int]) -> dict[str, int | float | None]:
    """NumPy定義に基づく、空配列安全な記述統計を返す。"""
    data = list(values)
    if not data:
        return {field: 0 if field == "count" else None for field in STATISTIC_FIELDS}
    array = np.asarray(data, dtype=np.int64)
    return {
        "count": int(array.size),
        "mean": _rounded(np.mean(array)),
        "median": _rounded(np.median(array)),
        "minimum": int(np.min(array)),
        "maximum": int(np.max(array)),
        "population_standard_deviation": _rounded(np.std(array, ddof=STANDARD_DEVIATION_DDOF)),
        "q1": _rounded(np.percentile(array, 25, method=PERCENTILE_METHOD)),
        "q3": _rounded(np.percentile(array, 75, method=PERCENTILE_METHOD)),
    }


def _group_summary(scope: str, group: str, records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    listed = list(records)
    return {
        "scope": scope,
        "group": group,
        "group_count": len(listed),
        "metrics": {metric: metric_statistics(record[metric] for record in listed) for metric in METRICS},
    }


def build_summary(source: Source) -> dict[str, Any]:
    """1つのcanonical summary objectを構築し、全formatがこれを共有する。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in source.records:
        group = UNKNOWN_GROUP if record["tcg_date"] is None else record["tcg_date"][:4]
        groups[group].append(record)
    years = sorted(group for group in groups if group != UNKNOWN_GROUP)
    ordered_groups = years + ([UNKNOWN_GROUP] if UNKNOWN_GROUP in groups else [])
    return {
        "schema_version": SUMMARY_JSON_SCHEMA_VERSION,
        "summary_identifier": SUMMARY_IDENTIFIER,
        "source_measurement_cache_key": source.metadata["measurement_cache_key"],
        "source_measurement_checksum": source.metadata["output_checksum"],
        "source_measurement_record_count": len(source.records),
        "metric_identifiers": {
            "character_count": source.metadata["character_metric_identifier"],
            "word_count": source.metadata["word_metric_identifier"],
            "sentence_count": source.metadata["sentence_metric_identifier"],
        },
        "metric_versions": {
            "character_count": source.metadata["character_metric_version"],
            "word_count": source.metadata["word_metric_version"],
            "sentence_count": source.metadata["sentence_metric_version"],
        },
        "grouping_definitions": {"identifier": GROUPING_IDENTIFIER, "unknown_group": UNKNOWN_GROUP},
        "statistic_definitions": {
            "identifier": STATISTIC_IDENTIFIER, "percentile_method": PERCENTILE_METHOD,
            "standard_deviation_ddof": STANDARD_DEVIATION_DDOF, "float_precision": FLOAT_PRECISION,
        },
        "overall": _group_summary("overall", "all", source.records),
        "by_tcg_year": [_group_summary("by_tcg_year", group, groups[group]) for group in ordered_groups],
    }


def summary_cache_key(source: Source) -> str:
    payload = {
        "summary_metadata_schema_version": SUMMARY_METADATA_SCHEMA_VERSION,
        "summary_json_schema_version": SUMMARY_JSON_SCHEMA_VERSION,
        "summary_csv_schema_version": SUMMARY_CSV_SCHEMA_VERSION,
        "summary_markdown_schema_version": SUMMARY_MARKDOWN_SCHEMA_VERSION,
        "source_measurement_cache_key": source.metadata["measurement_cache_key"],
        "source_measurement_checksum": source.metadata["output_checksum"],
        "source_record_count": source.metadata["measured_record_count"],
        "metric_identifiers": [source.metadata[f"{metric}_metric_identifier"] for metric in ("character", "word", "sentence")],
        "metric_versions": [source.metadata[f"{metric}_metric_version"] for metric in ("character", "word", "sentence")],
        "grouping_identifier": GROUPING_IDENTIFIER,
        "statistic_identifier": STATISTIC_IDENTIFIER,
        "percentile_method": PERCENTILE_METHOD,
        "standard_deviation_ddof": STANDARD_DEVIATION_DDOF,
        "float_precision": FLOAT_PRECISION,
        "output_formats": OUTPUT_FORMATS,
        "output_order": OUTPUT_ORDER,
        "unknown_group_policy": UNKNOWN_GROUP,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _summary_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in [summary["overall"], *summary["by_tcg_year"]]:
        for metric in METRICS:
            rows.append({"scope": group["scope"], "group": group["group"], "metric": metric, **group["metrics"][metric]})
    return rows


def serialize_json(summary: dict[str, Any]) -> bytes:
    return (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _csv_value(value: Any) -> str | int:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{FLOAT_PRECISION}f}"
    return value


def serialize_csv(summary: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in _summary_rows(summary):
        writer.writerow({field: _csv_value(row[field]) for field in CSV_FIELDS})
    return output.getvalue().encode("utf-8")


def _markdown_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{FLOAT_PRECISION}f}"
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", "\\`")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
    )


def serialize_markdown(summary: dict[str, Any]) -> bytes:
    header = "| " + " | ".join(CSV_FIELDS) + " |"
    separator = "|" + "|".join("---" for _ in CSV_FIELDS) + "|"
    lines = [header, separator]
    for row in _summary_rows(summary):
        lines.append("| " + " | ".join(_markdown_value(row[field]) for field in CSV_FIELDS) + " |")
    return ("\n".join(lines) + "\n").encode("utf-8")


def output_metadata_path(output: Path, key: str) -> Path:
    return output / f"summary-{key[:KEY_PREFIX_LENGTH]}.metadata.json"


def output_data_path(output: Path, key: str, checksum: str, suffix: str) -> Path:
    return output / f"summary-{key[:KEY_PREFIX_LENGTH]}-{checksum[:CONTENT_PREFIX_LENGTH]}.{suffix}"


def _format_identifier(format_name: str) -> str:
    return {
        "json": JSON_FORMAT_IDENTIFIER,
        "csv": CSV_FORMAT_IDENTIFIER,
        "markdown": MARKDOWN_FORMAT_IDENTIFIER,
    }[format_name]


def _expected_cache_metadata(source: Source, key: str) -> dict[str, Any]:
    unknown_count = sum(record["tcg_date"] is None for record in source.records)
    year_groups = {record["tcg_date"][:4] for record in source.records if record["tcg_date"] is not None}
    return {
        "metadata_schema_version": SUMMARY_METADATA_SCHEMA_VERSION,
        "completed": True,
        "summary_cache_key": key,
        "summary_identifier": SUMMARY_IDENTIFIER,
        "summary_json_schema_version": SUMMARY_JSON_SCHEMA_VERSION,
        "summary_csv_schema_version": SUMMARY_CSV_SCHEMA_VERSION,
        "summary_markdown_schema_version": SUMMARY_MARKDOWN_SCHEMA_VERSION,
        "source_measurement_metadata_file": source.metadata_path.name,
        "source_measurement_data_file": source.data_path.name,
        "source_measurement_cache_key": source.metadata["measurement_cache_key"],
        "source_measurement_checksum": source.metadata["output_checksum"],
        "source_measurement_record_count": len(source.records),
        "character_metric_identifier": source.metadata["character_metric_identifier"],
        "word_metric_identifier": source.metadata["word_metric_identifier"],
        "sentence_metric_identifier": source.metadata["sentence_metric_identifier"],
        "character_metric_version": source.metadata["character_metric_version"],
        "word_metric_version": source.metadata["word_metric_version"],
        "sentence_metric_version": source.metadata["sentence_metric_version"],
        "grouping_identifier": GROUPING_IDENTIFIER,
        "statistic_identifier": STATISTIC_IDENTIFIER,
        "percentile_method": PERCENTILE_METHOD,
        "standard_deviation_ddof": STANDARD_DEVIATION_DDOF,
        "float_precision": FLOAT_PRECISION,
        "unknown_group_policy": UNKNOWN_GROUP_POLICY,
        "output_ordering_identifier": OUTPUT_ORDER,
        "output_formats": list(OUTPUT_FORMAT_ORDER),
        "overall_count": len(source.records),
        "dated_record_count": len(source.records) - unknown_count,
        "unknown_date_count": unknown_count,
        "year_group_count": len(year_groups),
    }


def _valid_data_file(output: Path, key: str, metadata: dict[str, Any], format_name: str) -> bool:
    suffix = OUTPUT_SUFFIXES[format_name]
    checksum = metadata.get(f"{format_name}_output_checksum")
    size = metadata.get(f"{format_name}_output_file_size")
    filename = metadata.get(f"{format_name}_output_file")
    if not isinstance(checksum, str) or len(checksum) != 64 or not _is_non_negative_int(size):
        return False
    expected_name = output_data_path(output, key, checksum, suffix).name
    if filename != expected_name:
        return False
    path = _safe_child(output, metadata.get(f"{format_name}_output_file"))
    if path is None or not path.is_file():
        return False
    raw = path.read_bytes()
    if len(raw) != metadata.get(f"{format_name}_output_file_size"):
        return False
    if hashlib.sha256(raw).hexdigest() != metadata.get(f"{format_name}_output_checksum"):
        return False
    return not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"\n")


def valid_output(output: Path, key: str, source: Source) -> bool:
    """metadata commit pointerと3形式のgenerationが現入力・現定義に一致するか検証する。"""
    try:
        metadata = _read_json(output_metadata_path(output, key))
        if not isinstance(metadata, dict):
            return False
        expected = _expected_cache_metadata(source, key)
        integer_fields = {
            "metadata_schema_version", "summary_json_schema_version", "summary_csv_schema_version",
            "summary_markdown_schema_version", "source_measurement_record_count", "character_metric_version",
            "word_metric_version", "sentence_metric_version", "standard_deviation_ddof", "float_precision",
            "overall_count", "dated_record_count", "unknown_date_count", "year_group_count",
        }
        if metadata.get("completed") is not True or any(not _is_non_negative_int(metadata.get(field)) for field in integer_fields):
            return False
        if any(metadata.get(field) != value for field, value in expected.items()):
            return False
        if any(metadata.get(f"{name}_format_identifier") != _format_identifier(name) for name in OUTPUT_FORMAT_ORDER):
            return False
        return all(_valid_data_file(output, key, metadata, name) for name in OUTPUT_FORMAT_ORDER)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def summarize(
    input_metadata: Path,
    output: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    writer: AtomicWriter = _write_atomic,
) -> dict[str, Any]:
    source = load_source(input_metadata)
    key = summary_cache_key(source)
    metadata_path = output_metadata_path(output, key)
    hit = valid_output(output, key, source)
    if hit and not force and not dry_run:
        metadata = _read_json(metadata_path)
        return {"summary_cache_key": key, "source": source, "summary": None, "cache_hit": True, "status": "cache_hit", "output_metadata_path": metadata_path, "output_paths": {name: output / metadata[f"{name}_output_file"] for name in OUTPUT_FORMAT_ORDER}}
    summary = build_summary(source)
    plan = {"summary_cache_key": key, "source": source, "summary": summary, "cache_hit": hit, "output_metadata_path": metadata_path}
    if dry_run:
        return plan
    contents = {
        "json": serialize_json(summary),
        "csv": serialize_csv(summary),
        "markdown": serialize_markdown(summary),
    }
    paths = {
        name: output_data_path(output, key, hashlib.sha256(contents[name]).hexdigest(), OUTPUT_SUFFIXES[name])
        for name in OUTPUT_FORMAT_ORDER
    }
    created: list[Path] = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_FORMAT_ORDER:
            content = contents[name]
            path = paths[name]
            if path.exists():
                if not path.is_file() or path.read_bytes() != content:
                    raise OSError("同名のsummary generationが期待する内容と一致しません")
            else:
                writer(path, content)
                created.append(path)
        metadata = {
            "metadata_schema_version": SUMMARY_METADATA_SCHEMA_VERSION, "completed": True, "summary_cache_key": key,
            "summary_identifier": SUMMARY_IDENTIFIER,
            "summary_json_schema_version": SUMMARY_JSON_SCHEMA_VERSION, "summary_csv_schema_version": SUMMARY_CSV_SCHEMA_VERSION,
            "summary_markdown_schema_version": SUMMARY_MARKDOWN_SCHEMA_VERSION, "created_at": _utc_now(),
            "source_measurement_metadata_file": source.metadata_path.name, "source_measurement_data_file": source.data_path.name,
            "source_measurement_cache_key": source.metadata["measurement_cache_key"], "source_measurement_checksum": source.metadata["output_checksum"],
            "source_measurement_record_count": len(source.records), "character_metric_identifier": source.metadata["character_metric_identifier"],
            "word_metric_identifier": source.metadata["word_metric_identifier"], "sentence_metric_identifier": source.metadata["sentence_metric_identifier"],
            "character_metric_version": source.metadata["character_metric_version"], "word_metric_version": source.metadata["word_metric_version"],
            "sentence_metric_version": source.metadata["sentence_metric_version"], "grouping_identifier": GROUPING_IDENTIFIER,
            "statistic_identifier": STATISTIC_IDENTIFIER, "percentile_method": PERCENTILE_METHOD,
            "standard_deviation_ddof": STANDARD_DEVIATION_DDOF, "float_precision": FLOAT_PRECISION,
            "unknown_group_policy": UNKNOWN_GROUP_POLICY, "output_ordering_identifier": OUTPUT_ORDER,
            "overall_count": summary["overall"]["group_count"], "dated_record_count": sum(group["group_count"] for group in summary["by_tcg_year"] if group["group"] != UNKNOWN_GROUP),
            "unknown_date_count": next((group["group_count"] for group in summary["by_tcg_year"] if group["group"] == UNKNOWN_GROUP), 0),
            "year_group_count": sum(group["group"] != UNKNOWN_GROUP for group in summary["by_tcg_year"]), "output_formats": list(OUTPUT_FORMAT_ORDER),
            "project_version": PROJECT_VERSION, "numpy_version": np.__version__,
        }
        for name in OUTPUT_FORMAT_ORDER:
            content = contents[name]
            metadata[f"{name}_format_identifier"] = _format_identifier(name)
            metadata[f"{name}_output_file"] = paths[name].name
            metadata[f"{name}_output_checksum"] = hashlib.sha256(content).hexdigest()
            metadata[f"{name}_output_file_size"] = len(content)
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        for path in created:
            try:
                _best_effort_unlink(path)
            except OSError:
                pass
        raise SummarizeError("集計出力の保存に失敗しました。既存出力は変更していません") from exc
    return {**plan, "status": "summarized", "output_paths": paths}


def dry_run_lines(input_metadata: Path, output: Path, *, force: bool = False) -> list[str]:
    plan = summarize(input_metadata, output, force=force, dry_run=True)
    source: Source = plan["source"]
    summary = plan["summary"]
    assert summary is not None
    unknown = next((group["group_count"] for group in summary["by_tcg_year"] if group["group"] == UNKNOWN_GROUP), 0)
    return [
        f"input metadata path: {source.metadata_path}", f"resolved measurement JSONL path: {source.data_path}",
        f"source measurement cache key: {source.metadata['measurement_cache_key']}", f"source checksum: {source.metadata['output_checksum']}",
        f"source record count: {len(source.records)}", f"overall count: {summary['overall']['group_count']}",
        f"dated count: {len(source.records) - unknown}", f"unknown count: {unknown}", f"year group count: {len(summary['by_tcg_year']) - (1 if unknown else 0)}",
        f"group order: numeric years ascending, {UNKNOWN_GROUP} last", f"metrics: {','.join(METRICS)}",
        f"statistics: {STATISTIC_IDENTIFIER}", f"percentile method: {PERCENTILE_METHOD}", f"stddev ddof: {STANDARD_DEVIATION_DDOF}",
        f"float precision: {FLOAT_PRECISION}", f"output directory: {output}",
        f"output naming policy: summary-{plan['summary_cache_key'][:KEY_PREFIX_LENGTH]}-<content-sha256-prefix>.json/.csv/.md",
        f"summary metadata path: {plan['output_metadata_path']}", f"summary cache key: {plan['summary_cache_key']}",
        f"valid existing output: {'yes' if plan['cache_hit'] else 'no'}", f"--force: {'yes' if force else 'no'}",
        f"summary required: {'yes' if force or not plan['cache_hit'] else 'no'}",
    ]
