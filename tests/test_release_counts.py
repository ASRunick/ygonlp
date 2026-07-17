import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path

from ygonlp.measure import RECORD_FIELDS
from ygonlp.release_counts import analyze_release_counts, build_release_counts, serialize_csv


def record(card_id, **changes):
    value = {
        "schema_version": 1, "card_id": card_id, "name": f"Card {card_id}", "card_type": "Effect Monster",
        "frame_type": "effect", "tcg_date": "2020-01-01", "text_normalized": "Effect.",
        "character_count": 10, "word_count": 2, "sentence_count": 1,
    }
    value.update(changes)
    return {field: value[field] for field in RECORD_FIELDS}


def source_files(tmp_path: Path, records):
    content = ("\n".join(json.dumps(item, separators=(",", ":")) for item in records) + ("\n" if records else "")).encode()
    data = tmp_path / "measured.jsonl"
    data.write_bytes(content)
    metadata = {
        "metadata_schema_version": 1, "completed": True, "measurement_cache_key": "measurement-key",
        "measurement_record_schema_version": 1, "character_metric_version": 1, "word_metric_version": 1,
        "sentence_metric_version": 1, "character_metric_identifier": "python_len_unicode_code_points_v1",
        "word_metric_identifier": "unicode_alnum_internal_apostrophe_hyphen_grouped_numeric_comma_v1",
        "sentence_metric_identifier": "split_terminal_punctuation_v1", "sort_order": "card_id_ascending",
        "output_data_file": data.name, "output_checksum": hashlib.sha256(content).hexdigest(), "output_file_size": len(content),
        "measured_record_count": len(records), "input_record_count": len(records),
        "source_preprocessing_cache_key": "preprocess-key", "source_preprocessing_checksum": "a" * 64,
    }
    path = tmp_path / "measured.metadata.json"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def test_release_counts_zero_years_cumulative_order_and_exclusions(tmp_path):
    records = [
        record(1, tcg_date="2020-01-01", card_type="Spell Card"),
        record(2, tcg_date="2022-01-01", card_type="Effect Monster"),
        record(3, tcg_date="2022-02-01", card_type="Effect Monster"),
        record(4, tcg_date=None), record(5, tcg_date="2030-01-01"),
    ]
    result = build_release_counts(records, date(2022, 6, 1))
    assert result["missing_date_count"] == 1 and result["future_date_count"] == 1 and result["included_record_count"] == 3
    assert [(row["year"], row["release_count"], row["cumulative_release_count"], row["is_partial_year"]) for row in result["overall"]] == [
        ("2020", 1, 1, False), ("2021", 0, 1, False), ("2022", 2, 3, True)]
    assert [(row["year"], row["card_type"], row["release_count"], row["cumulative_release_count"]) for row in result["by_year_card_type"]] == [
        ("2020", "Effect Monster", 0, 0), ("2020", "Spell Card", 1, 1),
        ("2021", "Effect Monster", 0, 0), ("2021", "Spell Card", 0, 1),
        ("2022", "Effect Monster", 2, 2), ("2022", "Spell Card", 0, 1)]
    rows = list(csv.DictReader(io.StringIO(serialize_csv(result).decode())))
    assert [tuple(row[field] for field in ("scope", "year", "card_type")) for row in rows] == [
        ("overall", "2020", ""), ("overall", "2021", ""), ("overall", "2022", ""),
        ("year_card_type", "2020", "Effect Monster"), ("year_card_type", "2020", "Spell Card"),
        ("year_card_type", "2021", "Effect Monster"), ("year_card_type", "2021", "Spell Card"),
        ("year_card_type", "2022", "Effect Monster"), ("year_card_type", "2022", "Spell Card")]


def test_release_counts_december_31_is_full_and_empty_input_has_no_rows():
    full = build_release_counts([record(1, tcg_date="2022-01-01")], date(2022, 12, 31))
    assert full["overall"][-1]["is_partial_year"] is False
    empty = build_release_counts([record(1, tcg_date=None), record(2, tcg_date="2030-01-01")], date(2022, 1, 1))
    assert empty["overall"] == [] and empty["by_year_card_type"] == []


def test_release_counts_cache_force_and_dry_run_are_deterministic(tmp_path):
    metadata = source_files(tmp_path, [record(1, tcg_date="2020-01-01"), record(2, tcg_date="2021-01-01", card_type="Trap Card")])
    output = tmp_path / "out"
    planned = analyze_release_counts(metadata, output, dry_run=True, today=date(2021, 12, 31))
    assert planned["status"] == "planned" and not output.exists()
    first = analyze_release_counts(metadata, output, today=date(2021, 12, 31))
    contents = {name: path.read_bytes() for name, path in first["output_paths"].items()}
    second = analyze_release_counts(metadata, output, today=date(2021, 12, 31))
    assert second["status"] == "cache_hit"
    forced = analyze_release_counts(metadata, output, force=True, today=date(2021, 12, 31))
    assert first["output_paths"] == forced["output_paths"]
    assert contents == {name: path.read_bytes() for name, path in forced["output_paths"].items()}
