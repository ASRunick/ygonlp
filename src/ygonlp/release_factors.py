"""出典付き製品カタログと年別candidate release countを探索的に照合する。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .artifacts import best_effort_unlink, read_json, safe_child, write_bytes_atomic
from .summarize import OUTPUT_FORMAT_ORDER, OUTPUT_SUFFIXES, PROJECT_VERSION

RELEASE_FACTORS_METADATA_SCHEMA_VERSION = 1
RELEASE_FACTORS_JSON_SCHEMA_VERSION = 1
RELEASE_FACTORS_IDENTIFIER = "product_catalogue_candidate_release_factor_analysis_v1"
CATALOG_COLUMNS = ("product_id", "release_date", "product_category", "candidate_card_count", "source_url", "source_note")
YEARLY_COLUMNS = ("year", "is_partial_year", "release_count", "year_over_year_change", "catalogued_product_count", "catalogued_candidate_card_count", "uncatalogued_candidate_card_count", "catalogue_coverage_ratio", "active_product_category_count")
CATEGORY_COLUMNS = ("year", "product_category", "product_count", "candidate_card_count", "share_of_release_count")
CSV_COLUMNS = ("scope", *YEARLY_COLUMNS, "product_category", "product_count", "candidate_card_count", "share_of_release_count")
OUTPUT_ORDER = "yearly_year_ascending_then_category_year_ascending_category_ascending_v1"
AtomicWriter = Callable[[Path, bytes], None]


class ReleaseFactorsError(RuntimeError):
    """製品カタログまたはrelease factor分析入力のエラー。"""


def _read_catalog(path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseFactorsError("製品カタログCSVをUTF-8で読み込めません") from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
    except csv.Error as exc:
        raise ReleaseFactorsError("製品カタログCSVが不正です") from exc
    if reader.fieldnames != list(CATALOG_COLUMNS):
        raise ReleaseFactorsError(f"製品カタログCSVのheaderは次の固定順で必要です: {', '.join(CATALOG_COLUMNS)}")
    rows: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    try:
        for line, row in enumerate(reader, start=2):
            if set(row) != set(CATALOG_COLUMNS) or any(row[column] is None for column in CATALOG_COLUMNS):
                raise ReleaseFactorsError(f"製品カタログCSVの{line}行目の列数が不正です")
            product_id, release_date, category, count_text, source_url, source_note = (row[column].strip() for column in CATALOG_COLUMNS)
            if not product_id or product_id in identifiers:
                raise ReleaseFactorsError(f"製品カタログCSVの{line}行目のproduct_idは空でない一意な値で必要です")
            try:
                parsed_date = date.fromisoformat(release_date)
            except ValueError as exc:
                raise ReleaseFactorsError(f"製品カタログCSVの{line}行目のrelease_dateはYYYY-MM-DDで必要です") from exc
            if not category or not source_url.startswith(("https://", "http://")) or not source_note:
                raise ReleaseFactorsError(f"製品カタログCSVの{line}行目には非空のcategory、http(s) source_url、source_noteが必要です")
            try:
                count = int(count_text)
            except ValueError as exc:
                raise ReleaseFactorsError(f"製品カタログCSVの{line}行目のcandidate_card_countは0以上の整数で必要です") from exc
            if count < 0 or str(count) != count_text:
                raise ReleaseFactorsError(f"製品カタログCSVの{line}行目のcandidate_card_countは0以上の整数で必要です")
            identifiers.add(product_id)
            rows.append({"product_id": product_id, "release_date": parsed_date.isoformat(), "product_category": category, "candidate_card_count": count, "source_url": source_url, "source_note": source_note})
    except csv.Error as exc:
        raise ReleaseFactorsError("製品カタログCSVが不正です") from exc
    return rows, hashlib.sha256(raw).hexdigest()


def _load_release_counts(metadata_path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    try:
        metadata = read_json(metadata_path)
        if not isinstance(metadata, dict) or metadata.get("metadata_schema_version") != 1 or metadata.get("completed") is not True or metadata.get("release_counts_identifier") != "tcg_first_appearance_candidate_yearly_release_counts_v1":
            raise ValueError
        data_path = safe_child(metadata_path.parent, metadata.get("json_output_file"))
        expected_checksum = metadata.get("json_output_checksum")
        expected_size = metadata.get("json_output_file_size")
        if data_path is None or not data_path.is_file() or not isinstance(expected_checksum, str) or not isinstance(expected_size, int):
            raise ValueError
        raw = data_path.read_bytes()
        if len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != expected_checksum:
            raise ValueError
        result = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ReleaseFactorsError("検証済みrelease count metadataとJSON outputが必要です") from exc
    if not isinstance(result, dict) or result.get("release_counts_identifier") != metadata["release_counts_identifier"] or not isinstance(result.get("overall"), list):
        raise ReleaseFactorsError("release count JSON schemaが不正です")
    return metadata, result, hashlib.sha256(raw).hexdigest()


def build_release_factor_analysis(release_counts: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """年別candidate first-appearance countに、手動検証済み製品カタログを照合する。"""
    yearly_input: dict[str, dict[str, Any]] = {}
    for row in release_counts["overall"]:
        if not isinstance(row, dict) or not isinstance(row.get("year"), str) or not isinstance(row.get("release_count"), int) or row["release_count"] < 0 or not isinstance(row.get("is_partial_year"), bool) or row["year"] in yearly_input:
            raise ReleaseFactorsError("release count JSONのoverall rowが不正です")
        yearly_input[row["year"]] = row
    products: dict[str, list[dict[str, Any]]] = defaultdict(list)
    category_counts: Counter[tuple[str, str]] = Counter()
    category_products: Counter[tuple[str, str]] = Counter()
    outside_year_count = 0
    for row in catalog:
        year = row["release_date"][:4]
        if year not in yearly_input:
            outside_year_count += 1
            continue
        products[year].append(row)
        key = (year, row["product_category"])
        category_counts[key] += row["candidate_card_count"]
        category_products[key] += 1

    yearly: list[dict[str, Any]] = []
    prior_count: int | None = None
    for year in sorted(yearly_input):
        source = yearly_input[year]
        release_count = source["release_count"]
        candidate_count = sum(row["candidate_card_count"] for row in products[year])
        if candidate_count > release_count:
            raise ReleaseFactorsError(f"{year}年の製品カタログcandidate_card_count合計がrelease_countを超えています")
        yearly.append({
            "year": year, "is_partial_year": source["is_partial_year"], "release_count": release_count,
            "year_over_year_change": None if prior_count is None else release_count - prior_count,
            "catalogued_product_count": len(products[year]), "catalogued_candidate_card_count": candidate_count,
            "uncatalogued_candidate_card_count": release_count - candidate_count,
            "catalogue_coverage_ratio": round(candidate_count / release_count, 6) if release_count else None,
            "active_product_category_count": len({row["product_category"] for row in products[year]}),
        })
        prior_count = release_count
    categories = [
        {"year": year, "product_category": category, "product_count": category_products[(year, category)],
         "candidate_card_count": category_counts[(year, category)],
         "share_of_release_count": round(category_counts[(year, category)] / yearly_input[year]["release_count"], 6) if yearly_input[year]["release_count"] else None}
        for year, category in sorted(category_counts)
    ]
    return {"schema_version": RELEASE_FACTORS_JSON_SCHEMA_VERSION, "analysis_identifier": RELEASE_FACTORS_IDENTIFIER,
            "release_count_definition": release_counts.get("date_definition"), "current_date_cutoff": release_counts.get("current_date_cutoff"),
            "catalogue_row_count": len(catalog), "catalogue_rows_outside_release_count_years": outside_year_count,
            "output_ordering_identifier": OUTPUT_ORDER, "yearly": yearly, "by_year_product_category": categories}


def _serialize_json(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _serialize_csv(result: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in result["yearly"]:
        writer.writerow({"scope": "yearly", **row})
    for row in result["by_year_product_category"]:
        writer.writerow({"scope": "year_product_category", **row})
    return output.getvalue().encode("utf-8")


def _markdown_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value).replace("|", "\\|")


def _serialize_markdown(result: dict[str, Any]) -> bytes:
    lines = ["# Release-count factor exploration", "", "## Yearly reconciliation", "", "| " + " | ".join(YEARLY_COLUMNS) + " |", "|" + "|".join("---" for _ in YEARLY_COLUMNS) + "|"]
    for row in result["yearly"]:
        lines.append("| " + " | ".join(_markdown_value(row[column]) for column in YEARLY_COLUMNS) + " |")
    lines.extend(["", "## Product categories", "", "| " + " | ".join(CATEGORY_COLUMNS) + " |", "|" + "|".join("---" for _ in CATEGORY_COLUMNS) + "|"])
    for row in result["by_year_product_category"]:
        lines.append("| " + " | ".join(_markdown_value(row[column]) for column in CATEGORY_COLUMNS) + " |")
    lines.extend(["", "The product catalogue is external evidence supplied by the user. These reconciliations describe catalogue coverage and co-occurrence; they do not establish that a product category caused a change in annual release counts.", ""])
    return "\n".join(lines).encode("utf-8")


def _metadata_path(output: Path, key: str) -> Path:
    return output / f"release-factors-{key[:16]}.metadata.json"


def _output_path(output: Path, key: str, checksum: str, name: str) -> Path:
    return output / f"release-factors-{key[:16]}-{checksum[:16]}.{OUTPUT_SUFFIXES[name]}"


def _cache_key(release_metadata: dict[str, Any], release_checksum: str, catalog_checksum: str) -> str:
    payload = {"metadata_schema_version": RELEASE_FACTORS_METADATA_SCHEMA_VERSION, "json_schema_version": RELEASE_FACTORS_JSON_SCHEMA_VERSION,
               "release_counts_cache_key": release_metadata.get("release_counts_cache_key"), "release_counts_json_checksum": release_checksum,
               "catalog_csv_checksum": catalog_checksum, "output_order": OUTPUT_ORDER}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _valid_output(output: Path, key: str, release_metadata: dict[str, Any], release_checksum: str, catalog_checksum: str) -> dict[str, Path] | None:
    try:
        metadata = read_json(_metadata_path(output, key))
        expected = {"metadata_schema_version": RELEASE_FACTORS_METADATA_SCHEMA_VERSION, "completed": True, "analysis_cache_key": key,
                    "analysis_identifier": RELEASE_FACTORS_IDENTIFIER, "release_counts_cache_key": release_metadata["release_counts_cache_key"],
                    "release_counts_json_checksum": release_checksum, "product_catalog_checksum": catalog_checksum,
                    "output_ordering_identifier": OUTPUT_ORDER}
        if not isinstance(metadata, dict) or any(metadata.get(name) != value for name, value in expected.items()):
            return None
        paths: dict[str, Path] = {}
        for name in OUTPUT_FORMAT_ORDER:
            checksum, size, filename = metadata.get(f"{name}_output_checksum"), metadata.get(f"{name}_output_file_size"), metadata.get(f"{name}_output_file")
            path = safe_child(output, filename)
            if not isinstance(checksum, str) or not isinstance(size, int) or size < 0 or path is None or path != _output_path(output, key, checksum, name) or not path.is_file():
                return None
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum:
                return None
            paths[name] = path
        return paths
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def analyze_release_factors(release_metadata_path: Path, product_catalog: Path, output: Path, *, force: bool = False, dry_run: bool = False, writer: AtomicWriter = write_bytes_atomic) -> dict[str, Any]:
    release_metadata, release_counts, release_checksum = _load_release_counts(release_metadata_path)
    catalog, catalog_checksum = _read_catalog(product_catalog)
    key = _cache_key(release_metadata, release_checksum, catalog_checksum)
    metadata_path = _metadata_path(output, key)
    paths_hit = _valid_output(output, key, release_metadata, release_checksum, catalog_checksum)
    if paths_hit is not None and not force and not dry_run:
        return {"status": "cache_hit", "cache_hit": True, "analysis_cache_key": key, "output_metadata_path": metadata_path, "output_paths": paths_hit}
    result = build_release_factor_analysis(release_counts, catalog)
    plan = {"status": "planned", "cache_hit": paths_hit is not None, "analysis_cache_key": key, "result": result, "output_metadata_path": metadata_path}
    if dry_run:
        return plan
    contents = {"json": _serialize_json(result), "csv": _serialize_csv(result), "markdown": _serialize_markdown(result)}
    paths = {name: _output_path(output, key, hashlib.sha256(contents[name]).hexdigest(), name) for name in OUTPUT_FORMAT_ORDER}
    created: list[Path] = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_FORMAT_ORDER:
            if paths[name].exists():
                if not paths[name].is_file() or paths[name].read_bytes() != contents[name]:
                    raise OSError("同名のrelease factor outputが期待する内容と一致しません")
            else:
                writer(paths[name], contents[name]); created.append(paths[name])
        metadata = {"metadata_schema_version": RELEASE_FACTORS_METADATA_SCHEMA_VERSION, "completed": True, "analysis_cache_key": key,
                    "analysis_identifier": RELEASE_FACTORS_IDENTIFIER, "release_counts_metadata_file": release_metadata_path.name,
                    "release_counts_cache_key": release_metadata["release_counts_cache_key"], "release_counts_json_checksum": release_checksum,
                    "product_catalog_file": product_catalog.name, "product_catalog_checksum": catalog_checksum,
                    "catalogue_row_count": result["catalogue_row_count"], "catalogue_rows_outside_release_count_years": result["catalogue_rows_outside_release_count_years"],
                    "output_ordering_identifier": OUTPUT_ORDER, "output_formats": list(OUTPUT_FORMAT_ORDER), "project_version": PROJECT_VERSION}
        for name in OUTPUT_FORMAT_ORDER:
            metadata.update({f"{name}_output_file": paths[name].name, f"{name}_output_checksum": hashlib.sha256(contents[name]).hexdigest(), f"{name}_output_file_size": len(contents[name])})
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode())
    except OSError as exc:
        for path in created:
            best_effort_unlink(path)
        raise ReleaseFactorsError("release factor分析出力の保存に失敗しました。既存出力は変更していません") from exc
    return {**plan, "status": "analyzed", "output_paths": paths}


def dry_run_lines(release_metadata_path: Path, product_catalog: Path, output: Path, *, force: bool = False) -> list[str]:
    plan = analyze_release_factors(release_metadata_path, product_catalog, output, force=force, dry_run=True)
    result = plan["result"]
    return [f"release counts metadata path: {release_metadata_path}", f"product catalog path: {product_catalog}",
            f"catalogue row count: {result['catalogue_row_count']}", f"output directory: {output}",
            f"release factor cache key: {plan['analysis_cache_key']}", f"valid existing output: {'yes' if plan['cache_hit'] else 'no'}",
            f"--force: {'yes' if force else 'no'}", f"release factor analysis required: {'yes' if force or not plan['cache_hit'] else 'no'}"]
