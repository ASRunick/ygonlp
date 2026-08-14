import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path

from ygonlp.measure import RECORD_FIELDS
from ygonlp.timeseries import DATE_DEFINITION, OUTPUT_ORDER, analyze_timeseries, build_timeseries, serialize_csv, serialize_json, serialize_markdown


def record(card_id, **changes):
    value = {
        "schema_version": 1, "card_id": card_id, "name": f"Card {card_id}", "card_type": "Effect Monster",
        "frame_type": "effect", "tcg_date": "2020-01-01", "text_normalized": "Effect.",
        "character_count": 10, "word_count": 2, "sentence_count": 1,
    }
    value.update(changes)
    return {field: value[field] for field in RECORD_FIELDS}


def source_files(tmp_path: Path, records):
    content = ("\n".join(json.dumps(item, separators=(",", ":")) for item in records) + "\n").encode()
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


def test_timeseries_orders_and_excludes_missing_and_future_records(tmp_path):
    records = [
        record(1, tcg_date="2021-01-01", card_type="Spell Card", character_count=20),
        record(2, tcg_date=None, character_count=30),
        record(3, tcg_date="2020-01-01", card_type="Trap Card", character_count=4),
        record(4, tcg_date="2020-02-01", card_type="Effect Monster", character_count=8),
        record(5, tcg_date="2030-01-01", character_count=40),
    ]
    result = build_timeseries(records, date(2021, 12, 31))

    assert "candidate_date" in DATE_DEFINITION
    assert OUTPUT_ORDER == "by_tcg_year_then_by_tcg_year_card_type_year_ascending_card_type_ascending_metric_order_v1"
    assert result["missing_date_count"] == 1 and result["future_date_count"] == 1 and result["included_record_count"] == 3
    assert [group["year"] for group in result["by_tcg_year"]] == ["2020", "2021"]
    assert [(group["year"], group["card_type"]) for group in result["by_tcg_year_card_type"]] == [
        ("2020", "Effect Monster"), ("2020", "Trap Card"), ("2021", "Spell Card")]
    assert result["by_tcg_year"][0]["group_count"] == 2
    assert result["by_tcg_year"][0]["metrics"]["character_count"]["mean"] == 6.0

    metadata = source_files(tmp_path, records)
    first = analyze_timeseries(metadata, tmp_path / "out", today=date(2021, 12, 31))
    first_contents = {name: path.read_bytes() for name, path in first["output_paths"].items()}
    first_metadata = Path(first["output_metadata_path"]).read_bytes()
    second = analyze_timeseries(metadata, tmp_path / "out", today=date(2021, 12, 31), force=True)
    assert first["output_paths"] == second["output_paths"]
    assert first_contents == {name: path.read_bytes() for name, path in second["output_paths"].items()}
    assert first_metadata == Path(second["output_metadata_path"]).read_bytes()
    saved = json.loads(Path(first["output_metadata_path"]).read_text(encoding="utf-8"))
    assert "created_at" not in saved
    assert {field: saved[field] for field in ("missing_date_count", "future_date_count", "included_record_count")} == {
        "missing_date_count": 1, "future_date_count": 1, "included_record_count": 3}
    assert saved["trend_statistic_definitions"]["minimum_observations"] == 2
    assert saved["partial_current_year_included"] is False
    assert serialize_json(result).endswith(b"\n") and serialize_csv(result).endswith(b"\n") and serialize_markdown(result).endswith(b"\n")
    assert len(list(csv.DictReader(io.StringIO(serialize_csv(result).decode())))) == 87


def test_timeseries_trends_cover_positive_negative_flat_and_undefined_cases():
    records = [
        record(1, tcg_date="2020-01-01", character_count=10, word_count=30, sentence_count=5),
        record(2, tcg_date="2021-01-01", character_count=20, word_count=20, sentence_count=5),
        record(3, tcg_date="2022-01-01", character_count=30, word_count=10, sentence_count=5),
        record(4, tcg_date="2020-02-01", card_type="Spell Card", character_count=10, word_count=30, sentence_count=5),
    ]
    result = build_timeseries(records, date(2022, 6, 30))

    def trend(scope, card_type, metric):
        return next(item for item in result["trends"] if item["scope"] == scope and item["card_type"] == card_type
                    and item["metric"] == metric and item["annual_aggregate"] == "mean")

    positive = trend("by_tcg_year", None, "character_count")
    assert positive["observation_years"] == [2020, 2021, 2022]
    assert positive["annual_card_counts"] == [2, 1, 1]
    assert positive["pearson"]["coefficient"] > 0 and positive["spearman"]["coefficient"] == 1.0
    assert positive["linear_trend"] == {"status": "defined", "reason": None, "slope": 10.0, "intercept": -20190.0}

    negative = trend("by_tcg_year", None, "word_count")
    assert negative["pearson"]["coefficient"] < 0 and negative["spearman"]["coefficient"] == -1.0
    flat = trend("by_tcg_year", None, "sentence_count")
    assert flat["pearson"] == {"status": "undefined", "reason": "constant_annual_aggregate", "coefficient": None}
    assert flat["linear_trend"]["slope"] == 0.0
    undefined = trend("by_tcg_year_card_type", "Spell Card", "character_count")
    assert undefined["pearson"] == {"status": "undefined", "reason": "insufficient_observations", "coefficient": None}
    assert undefined["linear_trend"] == {"status": "undefined", "reason": "insufficient_observations", "slope": None, "intercept": None}
    assert result["partial_current_year_included"] is True
