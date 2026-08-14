import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path

import pytest

from ygonlp.measure import RECORD_FIELDS
from ygonlp.release_counts import analyze_release_counts
from ygonlp.release_factors import ReleaseFactorsError, analyze_release_factors, build_release_factor_analysis


def record(card_id: int, tcg_date: str):
    values = {"schema_version": 1, "card_id": card_id, "name": f"Card {card_id}", "card_type": "Effect Monster", "frame_type": "effect", "tcg_date": tcg_date, "text_normalized": "Effect.", "character_count": 7, "word_count": 1, "sentence_count": 1}
    return {field: values[field] for field in RECORD_FIELDS}


def measurement_metadata(tmp_path: Path) -> Path:
    records = [record(1, "2020-01-01"), record(2, "2020-06-01"), record(3, "2021-01-01")]
    raw = ("\n".join(json.dumps(row, separators=(",", ":")) for row in records) + "\n").encode()
    data = tmp_path / "measured.jsonl"
    data.write_bytes(raw)
    metadata = {"metadata_schema_version": 1, "completed": True, "measurement_cache_key": "measurement-key", "measurement_record_schema_version": 1, "character_metric_version": 1, "word_metric_version": 1, "sentence_metric_version": 1, "character_metric_identifier": "python_len_unicode_code_points_v1", "word_metric_identifier": "unicode_alnum_internal_apostrophe_hyphen_grouped_numeric_comma_v1", "sentence_metric_identifier": "split_terminal_punctuation_v1", "sort_order": "card_id_ascending", "output_data_file": data.name, "output_checksum": hashlib.sha256(raw).hexdigest(), "output_file_size": len(raw), "measured_record_count": len(records), "input_record_count": len(records), "source_preprocessing_cache_key": "preprocess-key", "source_preprocessing_checksum": "a" * 64}
    path = tmp_path / "measured.metadata.json"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def catalog(tmp_path: Path, rows: list[tuple[str, str, str, int, str, str]] | None = None) -> Path:
    rows = rows or [("core-2020", "2020-01-01", "core_booster", 2, "https://example.test/core-2020", "official product page"), ("deck-2021", "2021-01-01", "structure_deck", 1, "https://example.test/deck-2021", "official product page")]
    path = tmp_path / "products.csv"
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("product_id", "release_date", "product_category", "candidate_card_count", "source_url", "source_note"))
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return path


def release_metadata(tmp_path: Path) -> Path:
    result = analyze_release_counts(measurement_metadata(tmp_path), tmp_path / "release-counts", today=date(2021, 12, 31))
    return result["output_metadata_path"]


def test_build_release_factor_analysis_reconciles_years_categories_and_changes(tmp_path):
    metadata = release_metadata(tmp_path)
    release_json = json.loads((metadata.parent / json.loads(metadata.read_text())["json_output_file"]).read_text())
    result = build_release_factor_analysis(release_json, [
        {"product_id": "one", "release_date": "2020-01-01", "product_category": "core", "candidate_card_count": 1, "source_url": "https://example.test/one", "source_note": "source"},
        {"product_id": "two", "release_date": "2020-02-01", "product_category": "deck", "candidate_card_count": 1, "source_url": "https://example.test/two", "source_note": "source"},
        {"product_id": "three", "release_date": "2019-12-01", "product_category": "old", "candidate_card_count": 1, "source_url": "https://example.test/three", "source_note": "source"},
    ])
    assert result["catalogue_rows_outside_release_count_years"] == 1
    assert result["yearly"] == [
        {"year": "2020", "is_partial_year": False, "release_count": 2, "year_over_year_change": None, "catalogued_product_count": 2, "catalogued_candidate_card_count": 2, "uncatalogued_candidate_card_count": 0, "catalogue_coverage_ratio": 1.0, "active_product_category_count": 2},
        {"year": "2021", "is_partial_year": False, "release_count": 1, "year_over_year_change": -1, "catalogued_product_count": 0, "catalogued_candidate_card_count": 0, "uncatalogued_candidate_card_count": 1, "catalogue_coverage_ratio": 0.0, "active_product_category_count": 0},
    ]
    assert [(row["year"], row["product_category"], row["share_of_release_count"]) for row in result["by_year_product_category"]] == [("2020", "core", 0.5), ("2020", "deck", 0.5)]


def test_catalog_validation_and_excess_coverage_are_rejected(tmp_path):
    metadata = release_metadata(tmp_path)
    bad = catalog(tmp_path, [("same", "2020-01-01", "core", 1, "not-a-url", "source"), ("same", "2020-02-01", "core", 1, "https://example.test", "source")])
    with pytest.raises(ReleaseFactorsError):
        analyze_release_factors(metadata, bad, tmp_path / "out")
    excessive = catalog(tmp_path, [("too-many", "2020-01-01", "core", 3, "https://example.test", "source")])
    with pytest.raises(ReleaseFactorsError, match="超えています"):
        analyze_release_factors(metadata, excessive, tmp_path / "out")


def test_analysis_dry_run_output_cache_force_and_csv_shape(tmp_path):
    metadata = release_metadata(tmp_path)
    product_catalog = catalog(tmp_path)
    output = tmp_path / "out"
    planned = analyze_release_factors(metadata, product_catalog, output, dry_run=True)
    assert planned["status"] == "planned" and not output.exists()
    first = analyze_release_factors(metadata, product_catalog, output)
    assert first["status"] == "analyzed"
    rows = list(csv.DictReader(io.StringIO(first["output_paths"]["csv"].read_text(encoding="utf-8"))))
    assert [row["scope"] for row in rows] == ["yearly", "yearly", "year_product_category", "year_product_category"]
    second = analyze_release_factors(metadata, product_catalog, output)
    assert second["status"] == "cache_hit"
    forced = analyze_release_factors(metadata, product_catalog, output, force=True)
    assert forced["output_paths"] == first["output_paths"]
