import csv
import hashlib
import io
import json
import math
from pathlib import Path

import numpy as np
import pytest

import ygonlp.summarize as module
from ygonlp.measure import RECORD_FIELDS as MEASUREMENT_RECORD_FIELDS
from ygonlp.summarize import (
    SummarizeError,
    build_summary,
    dry_run_lines,
    load_source,
    metric_statistics,
    output_metadata_path,
    serialize_csv,
    serialize_json,
    serialize_markdown,
    summarize,
    summary_cache_key,
    valid_output,
)


def record(card_id: int = 1, **changes):
    value = {
        "schema_version": 1, "card_id": card_id, "name": f"Card {card_id}", "card_type": "Effect Monster",
        "frame_type": "effect", "tcg_date": "2002-03-08", "text_normalized": "Effect text.",
        "character_count": 12, "word_count": 2, "sentence_count": 1,
    }
    value.update(changes)
    return {field: value[field] for field in MEASUREMENT_RECORD_FIELDS}


def source_files(tmp_path: Path, records=None, *, metadata_changes=None):
    records = records if records is not None else [record()]
    content = b"" if not records else ("\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in records) + "\n").encode("utf-8")
    data_path = tmp_path / "cards-measured-source.jsonl"
    data_path.write_bytes(content)
    metadata = {
        "metadata_schema_version": 1, "completed": True, "measurement_cache_key": "measurement-key",
        "measurement_record_schema_version": 1, "character_metric_identifier": "python_len_unicode_code_points_v1",
        "word_metric_identifier": "unicode_alnum_internal_apostrophe_hyphen_grouped_numeric_comma_v1",
        "sentence_metric_identifier": "split_terminal_punctuation_v1", "character_metric_version": 1,
        "word_metric_version": 1, "sentence_metric_version": 1, "sort_order": "card_id_ascending",
        "output_data_file": data_path.name, "output_checksum": hashlib.sha256(content).hexdigest(),
        "output_file_size": len(content), "measured_record_count": len(records), "input_record_count": len(records),
        "source_preprocessing_cache_key": "preprocess-key", "source_preprocessing_checksum": "a" * 64,
    }
    metadata.update(metadata_changes or {})
    metadata_path = tmp_path / "cards-measured-source.metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return metadata_path, data_path


def test_numpy_runtime_dependency_is_available():
    assert np.__version__.split(".")[0] == "2"


def test_metric_statistics_uses_required_numpy_definitions_and_rounding():
    stats = metric_statistics([1, 2, 3, 4])
    assert stats == {
        "count": 4, "mean": 2.5, "median": 2.5, "minimum": 1, "maximum": 4,
        "population_standard_deviation": 1.118034, "q1": 1.75, "q3": 3.25,
    }
    assert metric_statistics([9])["population_standard_deviation"] == 0.0
    assert metric_statistics([]) == {
        "count": 0, "mean": None, "median": None, "minimum": None, "maximum": None,
        "population_standard_deviation": None, "q1": None, "q3": None,
    }


def test_build_summary_groups_years_and_unknown_last(tmp_path):
    metadata, _ = source_files(tmp_path, [
        record(1, tcg_date="2004-01-01", character_count=4),
        record(2, tcg_date=None, character_count=2),
        record(3, tcg_date="2002-01-01", character_count=8),
    ])
    summary = build_summary(load_source(metadata))
    assert summary["overall"]["group_count"] == 3
    assert [group["group"] for group in summary["by_tcg_year"]] == ["2002", "2004", "unknown"]
    assert summary["overall"]["metrics"]["character_count"]["mean"] == 4.666667


def test_empty_measurement_input_is_a_normal_summary(tmp_path):
    metadata, _ = source_files(tmp_path, [])
    result = summarize(metadata, tmp_path / "output")
    summary = result["summary"]
    assert summary["overall"]["group_count"] == 0 and summary["by_tcg_year"] == []
    assert summary["overall"]["metrics"]["word_count"]["mean"] is None
    saved = json.loads(Path(result["output_metadata_path"]).read_text(encoding="utf-8"))
    assert saved["overall_count"] == 0 and saved["year_group_count"] == 0
    csv_rows = list(csv.DictReader(io.StringIO(Path(result["output_paths"]["csv"]).read_text(encoding="utf-8"))))
    assert len(csv_rows) == 3 and all(row["count"] == "0" and row["mean"] == "" for row in csv_rows)
    assert valid_output(tmp_path / "output", result["summary_cache_key"], result["source"])


@pytest.mark.parametrize("change", [
    {"metadata_schema_version": 2}, {"completed": False}, {"measurement_cache_key": None},
    {"word_metric_identifier": None}, {"output_data_file": "../outside.jsonl"},
    {"output_data_file": "nested/data.jsonl"}, {"output_checksum": "0" * 64},
    {"output_file_size": 99}, {"measured_record_count": 2},
])
def test_load_source_rejects_invalid_metadata(tmp_path, change):
    metadata, _ = source_files(tmp_path, metadata_changes=change)
    with pytest.raises(SummarizeError):
        load_source(metadata)


@pytest.mark.parametrize("mutation", ["broken", "missing", "negative", "out_of_order", "invalid_date"])
def test_load_source_validates_measurement_jsonl(tmp_path, mutation):
    records = [record(1), record(2)]
    if mutation == "broken":
        metadata, data = source_files(tmp_path, records)
        data.write_bytes(b"{")
        saved = json.loads(metadata.read_text(encoding="utf-8"))
        saved["output_checksum"] = hashlib.sha256(b"{").hexdigest()
        saved["output_file_size"] = 1
        metadata.write_text(json.dumps(saved), encoding="utf-8")
    elif mutation == "missing":
        changed = record(2)
        changed.pop("name")
        metadata, _ = source_files(tmp_path, [record(1), changed])
    elif mutation == "negative":
        metadata, _ = source_files(tmp_path, [record(1, word_count=-1), record(2)])
    elif mutation == "out_of_order":
        metadata, _ = source_files(tmp_path, [record(2), record(1)])
    else:
        metadata, _ = source_files(tmp_path, [record(1, tcg_date="2002-99-99"), record(2)])
    with pytest.raises(SummarizeError):
        load_source(metadata)


def test_json_csv_markdown_are_deterministic_and_cross_format_consistent(tmp_path):
    metadata, _ = source_files(tmp_path, [record(1, name="A|B", tcg_date="2002-01-01"), record(2, tcg_date=None)])
    summary = build_summary(load_source(metadata))
    json_bytes = serialize_json(summary)
    csv_bytes = serialize_csv(summary)
    markdown_bytes = serialize_markdown(summary)
    assert json_bytes == serialize_json(summary) and csv_bytes == serialize_csv(summary) and markdown_bytes == serialize_markdown(summary)
    assert all(not content.startswith(b"\xef\xbb\xbf") and b"\r" not in content and content.endswith(b"\n") for content in (json_bytes, csv_bytes, markdown_bytes))
    parsed = json.loads(json_bytes)
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert len(rows) == (1 + len(parsed["by_tcg_year"])) * 3
    assert rows[0]["scope"] == "overall" and rows[0]["metric"] == "character_count"
    assert "1.000000" in csv_bytes.decode("utf-8") and "unknown" in markdown_bytes.decode("utf-8")


def test_summary_save_cache_force_and_dry_run(tmp_path, monkeypatch):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "output"
    plan = summarize(metadata, tmp_path / "dry", dry_run=True, force=True)
    assert not (tmp_path / "dry").exists() and plan["cache_hit"] is False
    first = summarize(metadata, output)
    assert valid_output(output, first["summary_cache_key"], first["source"])
    monkeypatch.setattr(module, "build_summary", lambda _: (_ for _ in ()).throw(AssertionError("must not summarize")))
    assert summarize(metadata, output)["status"] == "cache_hit"
    monkeypatch.undo()
    assert summarize(metadata, output, force=True)["status"] == "summarized"


@pytest.mark.parametrize("failure", ["json", "csv", "markdown", "metadata"])
def test_save_failure_keeps_old_valid_output(tmp_path, failure):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "output"
    first = summarize(metadata, output)
    old_metadata = Path(first["output_metadata_path"]).read_bytes()
    changed_source = tmp_path / "changed-source"
    changed_source.mkdir()
    changed_metadata, _ = source_files(changed_source, [record(1, character_count=99)])

    def fail(path, content):
        if failure == "metadata" and path.name.endswith("metadata.json"):
            raise OSError("metadata failure")
        if failure != "metadata" and path.suffix == f".{failure if failure != 'markdown' else 'md'}":
            raise OSError("data failure")
        module._write_atomic(path, content)

    with pytest.raises(SummarizeError):
        summarize(changed_metadata, output, writer=fail)
    assert Path(first["output_metadata_path"]).read_bytes() == old_metadata
    assert valid_output(output, first["summary_cache_key"], first["source"])


def test_valid_output_requires_all_files_and_identifiers(tmp_path):
    metadata, _ = source_files(tmp_path)
    result = summarize(metadata, tmp_path / "output")
    Path(result["output_paths"]["csv"]).unlink()
    assert not valid_output(tmp_path / "output", result["summary_cache_key"], result["source"])


def test_summary_cache_key_changes_with_definition_and_source(tmp_path, monkeypatch):
    metadata, _ = source_files(tmp_path)
    source = load_source(metadata)
    original = summary_cache_key(source)
    monkeypatch.setattr(module, "FLOAT_PRECISION", 5)
    assert summary_cache_key(source) != original
    monkeypatch.setattr(module, "FLOAT_PRECISION", 6)
    changed = dict(source.metadata, output_checksum="b" * 64)
    assert summary_cache_key(module.Source(source.metadata_path, source.data_path, changed, source.records)) != original


def test_dry_run_reports_required_fields(tmp_path):
    metadata, _ = source_files(tmp_path)
    lines = dry_run_lines(metadata, tmp_path / "missing", force=True)
    for field in ["overall count:", "dated count:", "unknown count:", "year group count:", "summary required:"]:
        assert any(line.startswith(field) for line in lines)


@pytest.mark.parametrize("field,value", [
    ("source_measurement_metadata_file", "changed.metadata.json"),
    ("source_measurement_data_file", "changed.jsonl"),
    ("source_measurement_cache_key", "changed"),
    ("character_metric_identifier", "changed"), ("word_metric_identifier", "changed"),
    ("sentence_metric_identifier", "changed"), ("character_metric_version", 2),
    ("word_metric_version", 2), ("sentence_metric_version", 2),
    ("grouping_identifier", "changed"), ("statistic_identifier", "changed"),
    ("percentile_method", "nearest"), ("standard_deviation_ddof", 1),
    ("float_precision", 5), ("unknown_group_policy", "changed"),
    ("output_ordering_identifier", "changed"), ("summary_json_schema_version", 2),
    ("summary_csv_schema_version", 2), ("summary_markdown_schema_version", 2),
    ("json_format_identifier", "changed"), ("csv_format_identifier", "changed"),
    ("markdown_format_identifier", "changed"), ("source_measurement_checksum", "b" * 64),
    ("source_measurement_record_count", 2), ("json_output_file", "other.json"),
    ("csv_output_file", "other.csv"), ("markdown_output_file", "other.md"),
    ("json_output_checksum", "b" * 64), ("csv_output_checksum", "b" * 64),
    ("markdown_output_checksum", "b" * 64), ("json_output_file_size", 1),
    ("csv_output_file_size", 1), ("markdown_output_file_size", 1),
])
def test_valid_output_rejects_metadata_contract_mutation(tmp_path, field, value):
    metadata, _ = source_files(tmp_path)
    result = summarize(metadata, tmp_path / "output")
    metadata_path = Path(result["output_metadata_path"])
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    saved[field] = value
    metadata_path.write_text(json.dumps(saved), encoding="utf-8")
    assert not valid_output(tmp_path / "output", result["summary_cache_key"], result["source"])


@pytest.mark.parametrize("field,value", [
    ("card_id", True), ("schema_version", True), ("schema_version", False), ("character_count", True),
    ("word_count", False), ("sentence_count", True),
])
def test_load_source_rejects_bool_measurement_integers(tmp_path, field, value):
    invalid = record(1)
    invalid[field] = value
    metadata, _ = source_files(tmp_path, [invalid])
    with pytest.raises(SummarizeError):
        load_source(metadata)


@pytest.mark.parametrize("field", ["measured_record_count", "output_file_size", "input_record_count"])
def test_load_source_rejects_bool_metadata_integers(tmp_path, field):
    metadata, _ = source_files(tmp_path, metadata_changes={field: True})
    with pytest.raises(SummarizeError):
        load_source(metadata)


@pytest.mark.parametrize("field", [
    "metadata_schema_version", "measurement_record_schema_version", "character_metric_version",
    "word_metric_version", "sentence_metric_version",
])
def test_load_source_rejects_bool_metadata_definition_integers(tmp_path, field):
    metadata, _ = source_files(tmp_path, metadata_changes={field: True})
    with pytest.raises(SummarizeError):
        load_source(metadata)


@pytest.mark.parametrize("field", ["source_measurement_record_count", "overall_count", "year_group_count"])
def test_valid_output_rejects_bool_summary_metadata_integers(tmp_path, field):
    metadata, _ = source_files(tmp_path)
    result = summarize(metadata, tmp_path / "output")
    metadata_path = Path(result["output_metadata_path"])
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    saved[field] = True
    metadata_path.write_text(json.dumps(saved), encoding="utf-8")
    assert not valid_output(tmp_path / "output", result["summary_cache_key"], result["source"])


def test_valid_output_rejects_non_bool_completed(tmp_path):
    metadata, _ = source_files(tmp_path)
    result = summarize(metadata, tmp_path / "output")
    metadata_path = Path(result["output_metadata_path"])
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    saved["completed"] = 1
    metadata_path.write_text(json.dumps(saved), encoding="utf-8")
    assert not valid_output(tmp_path / "output", result["summary_cache_key"], result["source"])


def test_cache_hit_preserves_hashes_and_mtimes(tmp_path):
    metadata, _ = source_files(tmp_path)
    result = summarize(metadata, tmp_path / "output")
    paths = [*result["output_paths"].values(), Path(result["output_metadata_path"])]
    before = [(hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths]
    assert summarize(metadata, tmp_path / "output")["status"] == "cache_hit"
    after = [(hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths]
    assert after == before


@pytest.mark.parametrize("records,groups", [
    ([record(1, tcg_date=None), record(2, tcg_date=None)], ["unknown"]),
    ([record(1, tcg_date="2002-01-01"), record(2, tcg_date="2004-01-01")], ["2002", "2004"]),
    ([record(1, tcg_date="2003-01-01")], ["2003"]),
])
def test_grouping_policy(tmp_path, records, groups):
    metadata, _ = source_files(tmp_path, records)
    summary = build_summary(load_source(metadata))
    assert [group["group"] for group in summary["by_tcg_year"]] == groups


def test_statistics_contract_and_python_scalars():
    stats = metric_statistics([10**12, 10**12 + 2, 10**12 + 4])
    assert all(value is None or isinstance(value, (int, float)) for value in stats.values())
    assert all(value is None or not isinstance(value, float) or math.isfinite(value) for value in stats.values())
    assert metric_statistics([1, 2])["q1"] == 1.25
    assert metric_statistics([1, 2])["q3"] == 1.75
    assert module._rounded(-0.00000001) == 0.0
    assert metric_statistics([7, 7, 7])["population_standard_deviation"] == 0.0


def test_csv_and_markdown_rendering_contract():
    summary = {
        "overall": {"scope": "overall", "group": 'a,b"c', "group_count": 1, "metrics": {
            metric: {"count": 1, "mean": 1.0, "median": 1.0, "minimum": 1, "maximum": 1,
                     "population_standard_deviation": 0.0, "q1": 1.0, "q3": 1.0} for metric in module.METRICS
        }},
        "by_tcg_year": [],
    }
    csv_text = serialize_csv(summary).decode("utf-8")
    markdown_text = serialize_markdown(summary).decode("utf-8")
    assert csv_text.splitlines()[0] == ",".join(module.CSV_FIELDS)
    assert '"a,b""c"' in csv_text and "1.000000" in csv_text
    assert markdown_text.splitlines()[0].startswith("| scope | group | metric |")
    assert markdown_text.splitlines()[1] == "|" + "|".join("---" for _ in module.CSV_FIELDS) + "|"
    assert markdown_text.endswith("\n") and "\r" not in markdown_text
    assert module._markdown_value("a|b\\c`d\r\ne\nf") == "a\\|b\\\\c\\`d<br>e<br>f"
    assert module._markdown_value(None) == "—"


def test_dependency_file_contract():
    root = Path(__file__).parents[1]
    assert 'numpy>=2.0,<3' in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert (root / "requirements.txt").read_text(encoding="utf-8").strip() == "-e ."
    assert (root / "requirements-dev.txt").read_text(encoding="utf-8").strip() == "-e .[dev]"
    environment = (root / "environment.yml").read_text(encoding="utf-8")
    assert "- python=3.11" in environment and "- pip:" in environment and "- -e .[dev]" in environment
    assert "numpy" not in environment.lower()


@pytest.mark.parametrize("target", ["json", "csv", "markdown", "metadata"])
def test_atomic_replace_failure_keeps_old_valid_output(tmp_path, monkeypatch, target):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "output"
    first = summarize(metadata, output)
    old_metadata = Path(first["output_metadata_path"]).read_bytes()
    changed_source = tmp_path / "changed-source"
    changed_source.mkdir()
    changed_metadata, _ = source_files(changed_source, [record(1, character_count=99)])
    original_replace = module.os.replace

    def fail_replace(temporary, destination):
        suffix = Path(destination).suffix
        if (target == "metadata" and str(destination).endswith(".metadata.json")) or (
            target != "metadata" and suffix == {"json": ".json", "csv": ".csv", "markdown": ".md"}[target]
        ):
            raise OSError("replace failure")
        original_replace(temporary, destination)

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(SummarizeError) as error:
        summarize(changed_metadata, output)
    assert isinstance(error.value.__cause__, OSError)
    assert Path(first["output_metadata_path"]).read_bytes() == old_metadata
    assert valid_output(output, first["summary_cache_key"], first["source"])


def test_cleanup_failure_does_not_replace_root_save_error(tmp_path, monkeypatch):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "output"
    first = summarize(metadata, output)
    changed_source = tmp_path / "changed-source"
    changed_source.mkdir()
    changed_metadata, _ = source_files(changed_source, [record(1, character_count=99)])

    def fail_writer(path, content):
        if path.suffix == ".csv":
            raise OSError("root write failure")
        module._write_atomic(path, content)

    monkeypatch.setattr(module, "_best_effort_unlink", lambda path: (_ for _ in ()).throw(OSError("cleanup failure")))
    with pytest.raises(SummarizeError) as error:
        summarize(changed_metadata, output, writer=fail_writer)
    assert isinstance(error.value.__cause__, OSError)
    assert "root write failure" in str(error.value.__cause__)
    assert valid_output(output, first["summary_cache_key"], first["source"])
