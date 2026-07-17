"""検証済み価格snapshotとmeasurementの決定論的な結合分析。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import numpy as np
import scipy
from scipy.stats import pearsonr, spearmanr

from .measure import _safe_child, _write_atomic
from .prices import (OBSERVATION_FIELDS, ORDERING_IDENTIFIER as SNAPSHOT_ORDERING, PRICE_OBSERVATION_SCHEMA_VERSION,
                     PRICE_SNAPSHOT_IDENTIFIER, PRICE_SNAPSHOT_METADATA_SCHEMA_VERSION, VENDORS)
from .summarize import (FLOAT_PRECISION, PERCENTILE_METHOD, STANDARD_DEVIATION_DDOF, STATISTIC_IDENTIFIER,
                        load_source as load_measurement_source)

PRICE_ANALYSIS_METADATA_SCHEMA_VERSION = 1
PRICE_ANALYSIS_JSON_SCHEMA_VERSION = 1
PRICE_ANALYSIS_CSV_SCHEMA_VERSION = 1
PRICE_ANALYSIS_MARKDOWN_SCHEMA_VERSION = 1
PRICE_ANALYSIS_IDENTIFIER = "vendor_currency_snapshot_price_metrics_analysis_v1"
KEY_PREFIX_LENGTH = CONTENT_PREFIX_LENGTH = 16
OUTPUT_FORMAT_ORDER = ("json", "csv", "markdown")
OUTPUT_SUFFIXES = {"json": "json", "csv": "csv", "markdown": "md"}
OUTPUT_ORDERING = "vendor_currency_ascending_overall_card_type_tcg_year_character_bucket_then_metric_ascending_v1"
CORRELATION_ORDERING = "vendor_currency_ascending_character_count_word_count_sentence_count_v1"
BUCKET_RULE = "integer_character_count_ranges_first_0_to_boundary_inclusive_subsequent_previous_plus_one_to_boundary_inclusive_final_greater_than_last_v1"
CSV_FIELDS = ("record_type", "vendor", "currency", "grouping", "group", "metric", "count", "mean", "median", "minimum", "maximum", "population_standard_deviation", "q1", "q3", "iqr", "correlation", "status", "reason")
AtomicWriter = Callable[[Path, bytes], None]


class PriceAnalysisError(RuntimeError):
    """価格分析の入力または保存エラー。"""


@dataclass(frozen=True)
class PriceSource:
    metadata_path: Path
    data_path: Path
    metadata: dict[str, Any]
    observations: list[dict[str, Any]]


def parse_character_buckets(value: str) -> tuple[int, ...]:
    try:
        boundaries = tuple(int(part) for part in value.split(","))
    except (AttributeError, ValueError) as exc:
        raise PriceAnalysisError("character_bucketsはcomma区切りの整数である必要があります") from exc
    if not boundaries or any(boundary <= 0 for boundary in boundaries) or any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        raise PriceAnalysisError("character_bucketsは重複なしの正の昇順整数である必要があります")
    return boundaries


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle: return json.load(handle)


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, str): raise PriceAnalysisError("snapshot decimal_priceが不正です")
    try: parsed = Decimal(value)
    except InvalidOperation as exc: raise PriceAnalysisError("snapshot decimal_priceが不正です") from exc
    if not parsed.is_finite() or parsed < 0: raise PriceAnalysisError("snapshot decimal_priceが不正です")
    return parsed


def load_price_source(metadata_path: Path) -> PriceSource:
    """Issue #13 metadata commit pointerとJSONLを完全に検証する。"""
    try: metadata = _read_json(metadata_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise PriceAnalysisError("price snapshot metadataを読み込めません") from exc
    if not isinstance(metadata, dict) or metadata.get("metadata_schema_version") != PRICE_SNAPSHOT_METADATA_SCHEMA_VERSION or metadata.get("completed") is not True:
        raise PriceAnalysisError("price snapshot metadataが不正です")
    if not isinstance(metadata.get("price_snapshot_cache_key"), str) or not isinstance(metadata.get("snapshot_timestamp"), str):
        raise PriceAnalysisError("price snapshot metadataのprovenanceが不正です")
    if metadata.get("snapshot_identifier") != PRICE_SNAPSHOT_IDENTIFIER or metadata.get("output_format") != "jsonl" or metadata.get("observation_schema_version") != PRICE_OBSERVATION_SCHEMA_VERSION or metadata.get("ordering_identifier") != SNAPSHOT_ORDERING or metadata.get("vendor_currency_mapping") != VENDORS:
        raise PriceAnalysisError("price snapshot metadataのschemaまたはvendor policyが不正です")
    data_path = _safe_child(metadata_path.parent, metadata.get("output_data_file"))
    checksum = metadata.get("output_checksum")
    if data_path is None or not data_path.is_file() or not isinstance(checksum, str) or len(checksum) != 64 or metadata.get("output_data_file") != f"price-snapshot-{metadata['price_snapshot_cache_key'][:16]}-{checksum[:16]}.jsonl": raise PriceAnalysisError("price snapshot data fileが不正です")
    try: raw = data_path.read_bytes()
    except OSError as exc: raise PriceAnalysisError("price snapshot JSONLを読み込めません") from exc
    if hashlib.sha256(raw).hexdigest() != checksum or type(metadata.get("output_file_size")) is not int or len(raw) != metadata.get("output_file_size") or b"\r" in raw or (raw and not raw.endswith(b"\n")):
        raise PriceAnalysisError("price snapshot JSONL checksumまたはformatが不正です")
    try: observations = [] if not raw else [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise PriceAnalysisError("price snapshot JSONLをparseできません") from exc
    if len(observations) != metadata.get("observation_count") or type(metadata.get("observation_count")) is not int:
        raise PriceAnalysisError("price snapshot observation countが不正です")
    previous: tuple[str, int, str] | None = None
    for observation in observations:
        if not isinstance(observation, dict) or tuple(observation) != OBSERVATION_FIELDS or observation.get("schema_version") != PRICE_OBSERVATION_SCHEMA_VERSION:
            raise PriceAnalysisError("price snapshot observation schemaが不正です")
        if type(observation.get("card_id")) is not int or not isinstance(observation.get("card_name"), str) or observation.get("vendor") not in VENDORS or observation.get("currency") != VENDORS[observation["vendor"]]:
            raise PriceAnalysisError("price snapshot observationが不正です")
        parsed = _decimal(observation.get("decimal_price"))
        if not isinstance(observation.get("raw_price"), str) or _decimal(observation["raw_price"]) != parsed or type(observation.get("is_zero_price")) is not bool or observation["is_zero_price"] != (parsed == 0):
            raise PriceAnalysisError("price snapshot price fieldが不正です")
        if observation.get("snapshot_timestamp") != metadata["snapshot_timestamp"] or observation.get("source_collection_cache_key") != metadata.get("source_collection_cache_key") or observation.get("source_payload_checksum") != metadata.get("source_payload_checksum"):
            raise PriceAnalysisError("price snapshot provenanceが不正です")
        current = (observation["snapshot_timestamp"], observation["card_id"], observation["vendor"])
        if previous is not None and current <= previous: raise PriceAnalysisError("price snapshot observation orderが不正です")
        previous = current
    return PriceSource(metadata_path, data_path, metadata, observations)


def _rounded(value: float) -> float:
    result = round(float(value), FLOAT_PRECISION)
    return 0.0 if result == 0 else result


def _numbers(values: list[Decimal]) -> np.ndarray:
    converted = np.asarray([float(value) for value in values], dtype=np.float64)
    if not np.all(np.isfinite(converted)): raise PriceAnalysisError("Decimal priceを有限float64へ変換できません")
    return converted


def price_statistics(values: list[Decimal]) -> dict[str, int | float | None]:
    if not values: return {field: 0 if field == "count" else None for field in ("count", "mean", "median", "minimum", "maximum", "population_standard_deviation", "q1", "q3", "iqr")}
    array = _numbers(values)
    q1, q3 = np.percentile(array, [25, 75], method=PERCENTILE_METHOD)
    return {"count": int(array.size), "mean": _rounded(np.mean(array)), "median": _rounded(np.median(array)),
            "minimum": _rounded(np.min(array)), "maximum": _rounded(np.max(array)),
            "population_standard_deviation": _rounded(np.std(array, ddof=STANDARD_DEVIATION_DDOF)),
            "q1": _rounded(q1), "q3": _rounded(q3), "iqr": _rounded(q3 - q1)}


def _bucket(value: int, boundaries: tuple[int, ...]) -> str:
    previous = 0
    for boundary in boundaries:
        if value <= boundary: return f"{previous}-{boundary}"
        previous = boundary + 1
    return f">{boundaries[-1]}"


def _correlation(values: list[Decimal], metrics: list[int], method: str) -> dict[str, Any]:
    if len(values) < 2: return {"status": "undefined", "reason": "insufficient_observations", "coefficient": None}
    x, y = _numbers(values), np.asarray(metrics, dtype=np.float64)
    if np.ptp(x) == 0 or np.ptp(y) == 0: return {"status": "undefined", "reason": "constant_variable", "coefficient": None}
    result = pearsonr(x, y) if method == "pearson" else spearmanr(x, y)
    return {"status": "defined", "reason": None, "coefficient": _rounded(float(result.statistic))}


def analyze_records(price_source: PriceSource, measurement_source: Any, *, boundaries: tuple[int, ...], include_zero: bool) -> dict[str, Any]:
    measures = {record["card_id"]: record for record in measurement_source.records}; price_ids = {item["card_id"] for item in price_source.observations}
    joined = [(item, measures[item["card_id"]], _decimal(item["decimal_price"])) for item in price_source.observations if item["card_id"] in measures]
    analyzed = [(item, record, value) for item, record, value in joined if include_zero or value != 0]
    groups: dict[tuple[str, str, str, str], list[Decimal]] = defaultdict(list)
    correlations: dict[tuple[str, str], list[tuple[Decimal, dict[str, Any]]]] = defaultdict(list)
    for observation, record, value in analyzed:
        vendor, currency = observation["vendor"], observation["currency"]
        groups[(vendor, currency, "overall", "all")].append(value)
        groups[(vendor, currency, "card_type", record["card_type"])].append(value)
        snapshot_day = price_source.metadata["snapshot_timestamp"][:10]
        if record["tcg_date"] is not None and record["tcg_date"] <= snapshot_day:
            groups[(vendor, currency, "tcg_year", record["tcg_date"][:4])].append(value)
        groups[(vendor, currency, "character_count_bucket", _bucket(record["character_count"], boundaries))].append(value)
        correlations[(vendor, currency)].append((value, record))
    statistics = [{"vendor": vendor, "currency": currency, "grouping": grouping, "group": group, "statistics": price_statistics(values)}
                  for (vendor, currency, grouping, group), values in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1], ("overall", "card_type", "tcg_year", "character_count_bucket").index(item[0][2]), item[0][3]))]
    correlation_rows = []
    for vendor, currency in sorted({(item["vendor"], item["currency"]) for item in price_source.observations}):
        values_records = correlations[(vendor, currency)]
        for metric in ("character_count", "word_count", "sentence_count"):
            values = [value for value, _ in values_records]; metrics = [record[metric] for _, record in values_records]
            for method in ("pearson", "spearman"):
                correlation_rows.append({"vendor": vendor, "currency": currency, "metric": metric, "method": method, **_correlation(values, metrics, method)})
    snapshot_zero_count = sum(_decimal(item["decimal_price"]) == 0 for item in price_source.observations)
    joined_zero_count = sum(value == 0 for _, _, value in joined)
    return {"schema_version": PRICE_ANALYSIS_JSON_SCHEMA_VERSION, "analysis_identifier": PRICE_ANALYSIS_IDENTIFIER,
            "snapshot_timestamp": price_source.metadata["snapshot_timestamp"], "include_zero": include_zero, "character_bucket_boundaries": list(boundaries),
            "coverage": {"total_snapshot_observation_count": len(price_source.observations), "joined_observation_count": len(joined),
                         "unmatched_price_card_ids": sorted(price_ids - set(measures)), "unmatched_measurement_card_ids": sorted(set(measures) - price_ids),
                         "snapshot_zero_observation_count": snapshot_zero_count, "joined_zero_observation_count": joined_zero_count,
                         "analyzed_observation_count": len(analyzed), "excluded_observation_count": len(price_source.observations) - len(analyzed)},
            "statistics": statistics, "correlations": correlation_rows,
            "tcg_year_policy": {"cutoff": price_source.metadata["snapshot_timestamp"][:10], "missing_or_future_excluded_from_year_groups": True}}


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in result["statistics"]:
        rows.append({"record_type": "statistics", "vendor": item["vendor"], "currency": item["currency"], "grouping": item["grouping"], "group": item["group"], **item["statistics"]})
    for item in result["correlations"]:
        rows.append({"record_type": "correlation", "vendor": item["vendor"], "currency": item["currency"], "metric": item["metric"], "correlation": item["method"], "status": item["status"], "reason": item["reason"], "mean": item["coefficient"]})
    return rows


def _serialize_json(result: dict[str, Any]) -> bytes: return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode()


def _serialize_csv(result: dict[str, Any]) -> bytes:
    output = io.StringIO(newline=""); writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n"); writer.writeheader()
    for row in _rows(result): writer.writerow({field: f"{row[field]:.{FLOAT_PRECISION}f}" if isinstance(row.get(field), float) else ("" if row.get(field) is None else row.get(field, "")) for field in CSV_FIELDS})
    return output.getvalue().encode()


def _serialize_markdown(result: dict[str, Any]) -> bytes:
    lines = ["| " + " | ".join(CSV_FIELDS) + " |", "|" + "|".join("---" for _ in CSV_FIELDS) + "|"]
    for row in _rows(result):
        lines.append("| " + " | ".join(("—" if row.get(field) is None else (f"{row[field]:.{FLOAT_PRECISION}f}" if isinstance(row.get(field), float) else str(row.get(field, "")))).replace("|", "\\|").replace("\n", "<br>") for field in CSV_FIELDS) + " |")
    return ("\n".join(lines) + "\n").encode()


def _key(price: PriceSource, measurement: Any, boundaries: tuple[int, ...], include_zero: bool) -> str:
    payload = {"metadata_schema_version": PRICE_ANALYSIS_METADATA_SCHEMA_VERSION, "analysis_identifier": PRICE_ANALYSIS_IDENTIFIER,
               "price_snapshot_checksum": price.metadata["output_checksum"], "price_snapshot_cache_key": price.metadata["price_snapshot_cache_key"],
               "measurement_checksum": measurement.metadata["output_checksum"], "measurement_cache_key": measurement.metadata["measurement_cache_key"],
               "boundaries": boundaries, "include_zero": include_zero, "statistic_identifier": STATISTIC_IDENTIFIER, "percentile_method": PERCENTILE_METHOD,
               "stddev_ddof": STANDARD_DEVIATION_DDOF, "scipy_version": scipy.__version__, "ordering": [OUTPUT_ORDERING, CORRELATION_ORDERING]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _metadata_path(output: Path, key: str) -> Path: return output / f"price-analysis-{key[:KEY_PREFIX_LENGTH]}.metadata.json"
def _data_path(output: Path, key: str, checksum: str, name: str) -> Path: return output / f"price-analysis-{key[:KEY_PREFIX_LENGTH]}-{checksum[:CONTENT_PREFIX_LENGTH]}.{name}"


def _expected(price: PriceSource, measurement: Any, key: str, boundaries: tuple[int, ...], include_zero: bool, result: dict[str, Any]) -> dict[str, Any]:
    return {"metadata_schema_version": PRICE_ANALYSIS_METADATA_SCHEMA_VERSION, "completed": True, "price_analysis_cache_key": key, "analysis_identifier": PRICE_ANALYSIS_IDENTIFIER,
            "json_schema_version": PRICE_ANALYSIS_JSON_SCHEMA_VERSION, "csv_schema_version": PRICE_ANALYSIS_CSV_SCHEMA_VERSION, "markdown_schema_version": PRICE_ANALYSIS_MARKDOWN_SCHEMA_VERSION,
            "source_price_metadata_file": price.metadata_path.name, "source_price_data_file": price.data_path.name, "source_price_snapshot_cache_key": price.metadata["price_snapshot_cache_key"], "source_price_snapshot_checksum": price.metadata["output_checksum"],
            "source_measurement_metadata_file": measurement.metadata_path.name, "source_measurement_data_file": measurement.data_path.name, "source_measurement_cache_key": measurement.metadata["measurement_cache_key"], "source_measurement_checksum": measurement.metadata["output_checksum"],
            "snapshot_timestamp": price.metadata["snapshot_timestamp"], "vendor_currency_policy": "separate_no_currency_conversion_no_vendor_merge_v1", "zero_policy": "included_in_coverage_excluded_from_statistics_and_correlations_unless_include_zero_v1",
            "include_zero": include_zero, "character_bucket_boundaries": list(boundaries), "character_bucket_rule": BUCKET_RULE,
            "statistic_definition": {"identifier": STATISTIC_IDENTIFIER, "percentile_method": PERCENTILE_METHOD, "standard_deviation_ddof": STANDARD_DEVIATION_DDOF, "float_precision": FLOAT_PRECISION, "decimal_to_float_boundary": "finite_decimal_to_float64_immediately_before_numpy_scipy_calculation_v1"},
            "correlation_definition": {"pearson": "scipy.stats.pearsonr", "spearman": "scipy.stats.spearmanr", "undefined_policy": "null_not_zero_for_insufficient_or_constant_v1"},
            "output_ordering_identifier": OUTPUT_ORDERING, "correlation_ordering_identifier": CORRELATION_ORDERING, "scipy_version": scipy.__version__, "numpy_version": np.__version__, "output_formats": list(OUTPUT_FORMAT_ORDER), "coverage": result["coverage"]}


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle: return json.load(handle)


def _valid(output: Path, key: str, price: PriceSource, measurement: Any, boundaries: tuple[int, ...], include_zero: bool, result: dict[str, Any]) -> bool:
    try:
        metadata = _read_json(_metadata_path(output, key))
        if not isinstance(metadata, dict) or any(metadata.get(field) != value for field, value in _expected(price, measurement, key, boundaries, include_zero, result).items()): return False
        for name in OUTPUT_FORMAT_ORDER:
            checksum, size, filename = metadata.get(f"{name}_output_checksum"), metadata.get(f"{name}_output_file_size"), metadata.get(f"{name}_output_file")
            if not isinstance(checksum, str) or type(size) is not int or filename != _data_path(output, key, checksum, OUTPUT_SUFFIXES[name]).name: return False
            path = _safe_child(output, filename)
            if path is None or not path.is_file() or (raw := path.read_bytes()) is None or len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum: return False
        return True
    except (OSError, json.JSONDecodeError, TypeError): return False


def analyze_prices(price_metadata: Path, measurement_metadata: Path, output: Path, *, character_buckets: str = "100,200,300,500", include_zero: bool = False, force: bool = False, writer: AtomicWriter = _write_atomic) -> dict[str, Any]:
    boundaries = parse_character_buckets(character_buckets)
    if type(include_zero) is not bool: raise PriceAnalysisError("include_zeroはboolである必要があります")
    price = load_price_source(price_metadata)
    try: measurement = load_measurement_source(measurement_metadata)
    except RuntimeError as exc: raise PriceAnalysisError(str(exc)) from exc
    result = analyze_records(price, measurement, boundaries=boundaries, include_zero=include_zero); key = _key(price, measurement, boundaries, include_zero); metadata_path = _metadata_path(output, key); hit = _valid(output, key, price, measurement, boundaries, include_zero, result)
    if hit and not force:
        metadata = _read_json(metadata_path)
        return {"status": "cache_hit", "cache_hit": True, "price_analysis_cache_key": key, "result": result, "output_metadata_path": metadata_path, "output_paths": {name: output / metadata[f"{name}_output_file"] for name in OUTPUT_FORMAT_ORDER}}
    contents = {"json": _serialize_json(result), "csv": _serialize_csv(result), "markdown": _serialize_markdown(result)}; paths = {name: _data_path(output, key, hashlib.sha256(contents[name]).hexdigest(), OUTPUT_SUFFIXES[name]) for name in OUTPUT_FORMAT_ORDER}; created: list[Path] = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_FORMAT_ORDER:
            if paths[name].exists():
                if not paths[name].is_file() or paths[name].read_bytes() != contents[name]: raise OSError("同名generationが期待する内容と一致しません")
            else: writer(paths[name], contents[name]); created.append(paths[name])
        metadata = _expected(price, measurement, key, boundaries, include_zero, result)
        for name in OUTPUT_FORMAT_ORDER: metadata[f"{name}_output_file"] = paths[name].name; metadata[f"{name}_output_checksum"] = hashlib.sha256(contents[name]).hexdigest(); metadata[f"{name}_output_file_size"] = len(contents[name])
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode())
    except OSError as exc:
        for path in created:
            try: path.unlink(missing_ok=True)
            except OSError: pass
        raise PriceAnalysisError("価格分析出力の保存に失敗しました。既存出力は変更していません") from exc
    return {"status": "analyzed", "cache_hit": hit, "price_analysis_cache_key": key, "result": result, "output_metadata_path": metadata_path, "output_paths": paths}
