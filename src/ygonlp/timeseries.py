"""測定済みLength MetricsのTCG初出候補年別記述統計。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import scipy
from scipy.stats import pearsonr, spearmanr

from .summarize import (
    FLOAT_PRECISION, METRICS, OUTPUT_FORMAT_ORDER, OUTPUT_SUFFIXES, PERCENTILE_METHOD,
    PROJECT_VERSION, STANDARD_DEVIATION_DDOF, STATISTIC_FIELDS, STATISTIC_IDENTIFIER,
    SummarizeError, _best_effort_unlink, _read_json, _rounded, _safe_child, _write_atomic,
    load_source, metric_statistics,
)

TIMESERIES_METADATA_SCHEMA_VERSION = 2
TIMESERIES_JSON_SCHEMA_VERSION = 2
TIMESERIES_IDENTIFIER = "effect_text_length_tcg_release_timeseries_v2"
GROUPING_IDENTIFIER = "tcg_year_and_tcg_year_card_type_ascending_v1"
DATE_DEFINITION = "tcg_date_adopted_tcg_set_source_first_release_candidate_date_v1"
CURRENT_DATE_CUTOFF_POLICY = "tcg_date_after_utc_current_date_excluded_v1"
OUTPUT_ORDER = "by_tcg_year_then_by_tcg_year_card_type_year_ascending_card_type_ascending_metric_order_v1"
TREND_MINIMUM_OBSERVATIONS = 2
TREND_AGGREGATES = ("mean", "median")
TREND_IDENTIFIER = "annual_metric_aggregate_year_pearson_spearman_ols_v1"
TREND_UNDEFINED_POLICY = "null_not_zero_for_insufficient_observations_or_constant_aggregate_v1"
CSV_FIELDS = ("record_type", "scope", "year", "card_type", "metric", *STATISTIC_FIELDS,
              "annual_aggregate", "observation_count", "observation_years", "annual_card_counts",
              "trend_method", "status", "reason", "coefficient", "slope", "intercept")
AtomicWriter = Callable[[Path, bytes], None]


class TimeSeriesError(RuntimeError):
    """時系列分析入力または保存のFatal error。"""


def _group(scope: str, year: str, card_type: str | None, records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    listed = list(records)
    return {
        "scope": scope, "year": year, "card_type": card_type, "group_count": len(listed),
        "metrics": {metric: metric_statistics(record[metric] for record in listed) for metric in METRICS},
    }


def _trend_statistic(years: list[int], values: list[float], method: str) -> dict[str, Any]:
    if len(years) < TREND_MINIMUM_OBSERVATIONS:
        return {"status": "undefined", "reason": "insufficient_observations", "coefficient": None}
    x = np.asarray(years, dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    if np.ptp(y) == 0:
        return {"status": "undefined", "reason": "constant_annual_aggregate", "coefficient": None}
    result = pearsonr(x, y) if method == "pearson" else spearmanr(x, y)
    return {"status": "defined", "reason": None, "coefficient": _rounded(result.statistic)}


def _linear_trend(years: list[int], values: list[float]) -> dict[str, Any]:
    if len(years) < TREND_MINIMUM_OBSERVATIONS:
        return {"status": "undefined", "reason": "insufficient_observations", "slope": None, "intercept": None}
    slope, intercept = np.polyfit(np.asarray(years, dtype=np.float64), np.asarray(values, dtype=np.float64), 1)
    return {"status": "defined", "reason": None, "slope": _rounded(slope), "intercept": _rounded(intercept)}


def _trends(groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_scope: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for group in groups:
        by_scope[(group["scope"], group["card_type"])].append(group)
    trends = []
    for (scope, card_type), scope_groups in sorted(by_scope.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        ordered = sorted(scope_groups, key=lambda group: group["year"])
        years = [int(group["year"]) for group in ordered]
        card_counts = [group["group_count"] for group in ordered]
        for metric in METRICS:
            for aggregate in TREND_AGGREGATES:
                values = [group["metrics"][metric][aggregate] for group in ordered]
                trends.append({
                    "scope": scope, "card_type": card_type, "metric": metric, "annual_aggregate": aggregate,
                    "observation_count": len(years), "observation_years": years, "annual_card_counts": card_counts,
                    "pearson": _trend_statistic(years, values, "pearson"),
                    "spearman": _trend_statistic(years, values, "spearman"),
                    "linear_trend": _linear_trend(years, values),
                })
    return trends


def build_timeseries(records: Iterable[dict[str, Any]], cutoff: date) -> dict[str, Any]:
    """released recordだけを年別・年×card_type別に決定論的に集計する。"""
    yearly: dict[str, list[dict[str, Any]]] = defaultdict(list)
    yearly_type: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    missing = future = 0
    for record in records:
        value = record["tcg_date"]
        if value is None:
            missing += 1
            continue
        if value > cutoff.isoformat():
            future += 1
            continue
        year = value[:4]
        yearly[year].append(record)
        yearly_type[(year, record["card_type"])].append(record)
    yearly_groups = [_group("by_tcg_year", year, None, yearly[year]) for year in sorted(yearly)]
    yearly_type_groups = [
        _group("by_tcg_year_card_type", year, card_type, yearly_type[(year, card_type)])
        for year, card_type in sorted(yearly_type)
    ]
    return {
        "schema_version": TIMESERIES_JSON_SCHEMA_VERSION,
        "timeseries_identifier": TIMESERIES_IDENTIFIER,
        "date_definition": DATE_DEFINITION,
        "current_date_cutoff": cutoff.isoformat(),
        "partial_current_year_included": cutoff != date(cutoff.year, 12, 31),
        "missing_date_count": missing,
        "future_date_count": future,
        "included_record_count": sum(len(value) for value in yearly.values()),
        "by_tcg_year": yearly_groups,
        "by_tcg_year_card_type": yearly_type_groups,
        "trends": _trends([*yearly_groups, *yearly_type_groups]),
        "statistic_definitions": {
            "identifier": STATISTIC_IDENTIFIER, "percentile_method": PERCENTILE_METHOD,
            "standard_deviation_ddof": STANDARD_DEVIATION_DDOF, "float_precision": FLOAT_PRECISION,
        },
        "trend_statistic_definitions": {
            "identifier": TREND_IDENTIFIER, "annual_aggregates": list(TREND_AGGREGATES),
            "minimum_observations": TREND_MINIMUM_OBSERVATIONS,
            "pearson": "scipy.stats.pearsonr_year_vs_annual_aggregate",
            "spearman": "scipy.stats.spearmanr_year_vs_annual_aggregate",
            "linear_trend": "numpy.polyfit_degree_1_year_vs_annual_aggregate",
            "undefined_policy": TREND_UNDEFINED_POLICY,
        },
    }


def timeseries_cache_key(source: Any, cutoff: date) -> str:
    payload = {
        "metadata_schema_version": TIMESERIES_METADATA_SCHEMA_VERSION,
        "json_schema_version": TIMESERIES_JSON_SCHEMA_VERSION,
        "source_measurement_cache_key": source.metadata["measurement_cache_key"],
        "source_measurement_checksum": source.metadata["output_checksum"],
        "metrics": METRICS, "grouping_identifier": GROUPING_IDENTIFIER,
        "date_definition": DATE_DEFINITION, "current_date_cutoff": cutoff.isoformat(),
        "current_date_cutoff_policy": CURRENT_DATE_CUTOFF_POLICY,
        "statistic_identifier": STATISTIC_IDENTIFIER, "trend_identifier": TREND_IDENTIFIER,
        "scipy_version": scipy.__version__, "numpy_version": np.__version__, "output_order": OUTPUT_ORDER,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for group in [*result["by_tcg_year"], *result["by_tcg_year_card_type"]]:
        for metric in METRICS:
            rows.append({"record_type": "descriptive", "scope": group["scope"], "year": group["year"], "card_type": group["card_type"] or "", "metric": metric, **group["metrics"][metric]})
    for trend in result["trends"]:
        common = {"record_type": "trend", "scope": trend["scope"], "card_type": trend["card_type"] or "", "metric": trend["metric"],
                  "annual_aggregate": trend["annual_aggregate"], "observation_count": trend["observation_count"],
                  "observation_years": ",".join(map(str, trend["observation_years"])), "annual_card_counts": ",".join(map(str, trend["annual_card_counts"]))}
        for method in ("pearson", "spearman"):
            rows.append({**common, "trend_method": method, **trend[method]})
        rows.append({**common, "trend_method": "linear", **trend["linear_trend"]})
    return rows


def _value(value: Any) -> str | int:
    if value is None:
        return ""
    return f"{value:.{FLOAT_PRECISION}f}" if isinstance(value, float) else value


def serialize_json(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def serialize_csv(result: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in _rows(result):
        writer.writerow({field: _value(row.get(field, "")) for field in CSV_FIELDS})
    return output.getvalue().encode("utf-8")


def serialize_markdown(result: dict[str, Any]) -> bytes:
    lines = ["| " + " | ".join(CSV_FIELDS) + " |", "|" + "|".join("---" for _ in CSV_FIELDS) + "|"]
    for row in _rows(result):
        lines.append("| " + " | ".join(str(_value(row.get(field, ""))).replace("|", "\\|") for field in CSV_FIELDS) + " |")
    lines.extend(["", "## Trend statistics", ""])
    for trend in result["trends"]:
        label = f"{trend['scope']} / {trend['card_type'] or 'all'} / {trend['metric']} / annual {trend['annual_aggregate']}"
        lines.append(f"- {label}: years={','.join(map(str, trend['observation_years']))}; annual_card_counts={','.join(map(str, trend['annual_card_counts']))}; Pearson={_value(trend['pearson']['coefficient']) or 'undefined'}; Spearman={_value(trend['spearman']['coefficient']) or 'undefined'}; slope={_value(trend['linear_trend']['slope']) or 'undefined'}; intercept={_value(trend['linear_trend']['intercept']) or 'undefined'}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def output_metadata_path(output: Path, key: str) -> Path:
    return output / f"timeseries-{key[:16]}.metadata.json"


def output_data_path(output: Path, key: str, checksum: str, suffix: str) -> Path:
    return output / f"timeseries-{key[:16]}-{checksum[:16]}.{suffix}"


def _valid_output(output: Path, key: str, source: Any, cutoff: date) -> bool:
    try:
        metadata = _read_json(output_metadata_path(output, key))
        expected = {"metadata_schema_version": TIMESERIES_METADATA_SCHEMA_VERSION, "completed": True,
                    "timeseries_cache_key": key, "timeseries_identifier": TIMESERIES_IDENTIFIER,
                    "source_measurement_cache_key": source.metadata["measurement_cache_key"],
                    "source_measurement_checksum": source.metadata["output_checksum"],
                    "date_definition": DATE_DEFINITION, "current_date_cutoff": cutoff.isoformat(),
                    "current_date_cutoff_policy": CURRENT_DATE_CUTOFF_POLICY, "grouping_identifier": GROUPING_IDENTIFIER,
                    "partial_current_year_included": cutoff != date(cutoff.year, 12, 31),
                    "trend_statistic_definitions": {
                        "identifier": TREND_IDENTIFIER, "annual_aggregates": list(TREND_AGGREGATES),
                        "minimum_observations": TREND_MINIMUM_OBSERVATIONS,
                        "pearson": "scipy.stats.pearsonr_year_vs_annual_aggregate",
                        "spearman": "scipy.stats.spearmanr_year_vs_annual_aggregate",
                        "linear_trend": "numpy.polyfit_degree_1_year_vs_annual_aggregate",
                        "undefined_policy": TREND_UNDEFINED_POLICY,
                    },
                    "scipy_version": scipy.__version__, "numpy_version": np.__version__,
                    "output_ordering_identifier": OUTPUT_ORDER}
        if not isinstance(metadata, dict) or any(metadata.get(name) != value for name, value in expected.items()):
            return False
        for name in OUTPUT_FORMAT_ORDER:
            checksum, size, filename = (metadata.get(f"{name}_output_checksum"), metadata.get(f"{name}_output_file_size"), metadata.get(f"{name}_output_file"))
            path = _safe_child(output, filename)
            if not isinstance(checksum, str) or not isinstance(size, int) or filename != output_data_path(output, key, checksum, OUTPUT_SUFFIXES[name]).name or path is None:
                return False
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum:
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def analyze_timeseries(input_metadata: Path, output: Path, *, force: bool = False, dry_run: bool = False, today: date | None = None, writer: AtomicWriter = _write_atomic) -> dict[str, Any]:
    try:
        source = load_source(input_metadata)
    except SummarizeError as exc:
        raise TimeSeriesError(str(exc)) from exc
    cutoff = today or datetime.now(timezone.utc).date()
    key = timeseries_cache_key(source, cutoff)
    metadata_path = output_metadata_path(output, key)
    hit = _valid_output(output, key, source, cutoff)
    if hit and not force and not dry_run:
        metadata = _read_json(metadata_path)
        return {"status": "cache_hit", "cache_hit": True, "timeseries_cache_key": key, "source": source, "output_metadata_path": metadata_path, "output_paths": {name: output / metadata[f"{name}_output_file"] for name in OUTPUT_FORMAT_ORDER}}
    result = build_timeseries(source.records, cutoff)
    plan = {"status": "planned", "cache_hit": hit, "timeseries_cache_key": key, "source": source, "result": result, "output_metadata_path": metadata_path}
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
                    raise OSError("同名のtimeseries generationが期待する内容と一致しません")
            else:
                writer(paths[name], contents[name]); created.append(paths[name])
        metadata = {"metadata_schema_version": TIMESERIES_METADATA_SCHEMA_VERSION, "completed": True, "timeseries_cache_key": key,
                    "timeseries_identifier": TIMESERIES_IDENTIFIER,
                    "source_measurement_metadata_file": source.metadata_path.name, "source_measurement_data_file": source.data_path.name,
                    "source_measurement_cache_key": source.metadata["measurement_cache_key"], "source_measurement_checksum": source.metadata["output_checksum"],
                    "date_definition": DATE_DEFINITION, "current_date_cutoff": cutoff.isoformat(), "current_date_cutoff_policy": CURRENT_DATE_CUTOFF_POLICY,
                    "missing_date_count": result["missing_date_count"], "future_date_count": result["future_date_count"], "included_record_count": result["included_record_count"],
                    "partial_current_year_included": result["partial_current_year_included"],
                    "grouping_identifier": GROUPING_IDENTIFIER, "output_ordering_identifier": OUTPUT_ORDER,
                    "statistic_definitions": result["statistic_definitions"], "trend_statistic_definitions": result["trend_statistic_definitions"],
                    "scipy_version": scipy.__version__, "numpy_version": np.__version__,
                    "output_formats": list(OUTPUT_FORMAT_ORDER), "project_version": PROJECT_VERSION}
        for name in OUTPUT_FORMAT_ORDER:
            metadata.update({f"{name}_output_file": paths[name].name, f"{name}_output_checksum": hashlib.sha256(contents[name]).hexdigest(), f"{name}_output_file_size": len(contents[name])})
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        for path in created: _best_effort_unlink(path)
        raise TimeSeriesError("時系列分析出力の保存に失敗しました。既存出力は変更していません") from exc
    return {**plan, "status": "analyzed", "output_paths": paths}


def dry_run_lines(input_metadata: Path, output: Path, *, force: bool = False) -> list[str]:
    plan = analyze_timeseries(input_metadata, output, force=force, dry_run=True)
    result = plan["result"]
    return [f"input metadata path: {input_metadata}", f"included record count: {result['included_record_count']}", f"missing date count: {result['missing_date_count']}", f"future date count: {result['future_date_count']}", f"current date cutoff: {result['current_date_cutoff']}", f"timeseries required: {'yes' if force or not plan['cache_hit'] else 'no'}"]
