import hashlib
import json
from pathlib import Path

import pytest

import ygonlp.measure as module
from ygonlp.measure import (
    MeasureError,
    character_count,
    dry_run_lines,
    load_source,
    measure,
    measurement_cache_key,
    output_metadata_path,
    sentence_count,
    valid_output,
    word_count,
)
from ygonlp.preprocess import RECORD_FIELDS as PREPROCESSING_RECORD_FIELDS


def card(card_id: int = 1, **changes):
    value = {
        "schema_version": 1, "card_id": card_id, "name": f"Card {card_id}",
        "card_type": "Effect Monster", "frame_type": "effect", "race": "Warrior",
        "archetype": None, "text_raw": "Effect text.", "text_normalized": "Effect text.",
        "text_kind": "effect_or_rule_text", "has_text": True, "is_effect_text_target": True,
        "exclusion_reason": None, "tcg_date": "2002-01-01", "ocg_date": None, "source_index": card_id,
    }
    value.update(changes)
    return {field: value[field] for field in PREPROCESSING_RECORD_FIELDS}


def source_files(tmp_path: Path, records=None, *, metadata_changes=None):
    records = records if records is not None else [card()]
    content = b"" if not records else ("\n".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records) + "\n").encode()
    data_path = tmp_path / "cards-normalized-source.jsonl"
    data_path.write_bytes(content)
    metadata = {
        "metadata_schema_version": 1, "completed": True, "preprocessing_cache_key": "preprocess-key",
        "record_schema_version": 1, "sort_order": "card_id_ascending", "output_data_file": data_path.name,
        "output_sha256": hashlib.sha256(content).hexdigest(), "output_record_count": len(records),
    }
    metadata.update(metadata_changes or {})
    metadata_path = tmp_path / "cards-normalized-source.metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return metadata_path, data_path


@pytest.mark.parametrize(("text", "expected"), [
    ("ABC", 3), ("A B", 3), ("A\nB", 3), ("é", 1), ("e\u0301", 2),
])
def test_character_count_uses_unicode_code_points(text, expected):
    assert character_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("ordinary words", 2), ("X-Saber", 1), ("once-per-turn", 1), ("opponent's", 1),
    ("opponent’s", 1), ("1,000", 1), ("1,000,000", 1), ("ATK/DEF", 2),
    ("Quick-Play Spell", 2), ("card:draw; ('card')", 3), ("", 0), ("éclair 漢字", 1),
])
def test_word_count_uses_fixed_ascii_pattern(text, expected):
    assert word_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("Draw 1 card.", 1), ("Destroy it. Then draw 1 card.", 2), ("Is this valid? Yes!", 2),
    ("Cost; effect", 1), ("First line\nSecond line", 1), ("No final delimiter", 1),
    ("What?! Really...", 2), ("...", 0), ("", 0),
])
def test_sentence_count_uses_terminal_delimiter_heuristic(text, expected):
    assert sentence_count(text) == expected


@pytest.mark.parametrize("change", [
    {"metadata_schema_version": 2}, {"completed": False}, {"output_data_file": None},
    {"output_data_file": "../outside.jsonl"}, {"output_data_file": "nested/file.jsonl"},
    {"output_data_file": str(Path("C:/outside.jsonl"))}, {"output_sha256": "0" * 64},
    {"output_record_count": 2}, {"preprocessing_cache_key": None}, {"record_schema_version": 2},
    {"sort_order": "other"},
])
def test_load_source_rejects_invalid_metadata(tmp_path, change):
    metadata, _ = source_files(tmp_path, metadata_changes=change)
    with pytest.raises(MeasureError):
        load_source(metadata)


@pytest.mark.parametrize("mutation", ["broken_jsonl", "missing_field", "wrong_type", "out_of_order", "duplicate"])
def test_load_source_validates_all_jsonl_records(tmp_path, mutation):
    records = [card(1), card(2)]
    if mutation == "broken_jsonl":
        metadata, data = source_files(tmp_path, records)
        data.write_bytes(b"{")
    elif mutation == "missing_field":
        changed = card(2)
        changed.pop("name")
        metadata, _ = source_files(tmp_path, [card(1), changed])
    elif mutation == "wrong_type":
        metadata, _ = source_files(tmp_path, [card(1, name=None), card(2)])
    elif mutation == "out_of_order":
        metadata, _ = source_files(tmp_path, [card(2), card(1)])
    else:
        metadata, _ = source_files(tmp_path, [card(1), card(1)])
    if mutation == "broken_jsonl":
        meta = json.loads(metadata.read_text(encoding="utf-8"))
        meta["output_sha256"] = hashlib.sha256(b"{").hexdigest()
        metadata.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(MeasureError):
        load_source(metadata)


def test_preprocessing_zero_record_contract_is_rejected(tmp_path):
    metadata, _ = source_files(tmp_path, [])
    with pytest.raises(MeasureError, match="0件"):
        load_source(metadata)


def test_measure_selects_only_nonblank_targets_and_writes_metadata(tmp_path):
    metadata, _ = source_files(tmp_path, [
        card(1, text_normalized="Draw 1 card."),
        card(2, is_effect_text_target=False),
        card(3, text_normalized=""),
        card(4, text_normalized="   "),
    ])
    result = measure(metadata, tmp_path / "output")
    output = Path(result["output_data_path"])
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert list(record) == [
        "schema_version", "card_id", "name", "card_type", "frame_type", "tcg_date", "text_normalized",
        "character_count", "word_count", "sentence_count",
    ]
    assert record["card_id"] == 1
    assert record["character_count"] == len("Draw 1 card.")
    assert record["word_count"] == 3
    assert record["sentence_count"] == 1
    saved_metadata = json.loads(Path(result["output_metadata_path"]).read_text(encoding="utf-8"))
    assert saved_metadata["input_record_count"] == 4
    assert saved_metadata["measured_record_count"] == 1
    assert saved_metadata["excluded_record_count"] == 3
    assert saved_metadata["empty_target_text_count"] == 2


def test_measure_supports_empty_output_and_cache_hit(tmp_path):
    metadata, _ = source_files(tmp_path, [card(1, is_effect_text_target=False), card(2, text_normalized="")])
    output = tmp_path / "output"
    first = measure(metadata, output)
    data = Path(first["output_data_path"])
    assert data.read_bytes() == b""
    saved_metadata = json.loads(Path(first["output_metadata_path"]).read_text(encoding="utf-8"))
    assert saved_metadata["measured_record_count"] == 0
    assert saved_metadata["output_checksum"] == hashlib.sha256(b"").hexdigest()
    assert valid_output(output, first["measurement_cache_key"])
    assert measure(metadata, output)["status"] == "cache_hit"


def test_measurement_cache_key_changes_with_versions_and_input_checksum(tmp_path, monkeypatch):
    metadata, _ = source_files(tmp_path)
    source = load_source(metadata)
    original = measurement_cache_key(source)
    monkeypatch.setattr(module, "WORD_METRIC_VERSION", 2)
    assert measurement_cache_key(source) != original
    monkeypatch.setattr(module, "WORD_METRIC_VERSION", 1)
    changed = dict(source.metadata, output_sha256="1" * 64)
    changed_source = module.Source(source.metadata_path, source.data_path, changed, source.records)
    assert measurement_cache_key(changed_source) != original


def test_jsonl_is_deterministic_and_utf8_lf_without_bom(tmp_path):
    metadata, _ = source_files(tmp_path, [card(1, name="非ASCII", text_normalized="é\nDraw.")])
    first = measure(metadata, tmp_path / "a")
    second = measure(metadata, tmp_path / "b")
    first_data = Path(first["output_data_path"]).read_bytes()
    second_data = Path(second["output_data_path"]).read_bytes()
    assert first_data == second_data
    assert not first_data.startswith(b"\xef\xbb\xbf") and b"\r" not in first_data and first_data.endswith(b"\n")
    assert "非ASCII" in first_data.decode("utf-8")


def test_dry_run_is_read_only_and_reports_plan(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "does-not-exist"
    lines = dry_run_lines(metadata, output, force=True)
    assert not output.exists()
    for field in ["input metadata path:", "measurement target count:", "excluded count:", "metric versions:", "measurement required:"]:
        assert any(line.startswith(field) for line in lines)


def test_cache_hit_does_not_remeasure(tmp_path, monkeypatch):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "output"
    first = measure(metadata, output)
    monkeypatch.setattr(module, "measure_records", lambda _: (_ for _ in ()).throw(AssertionError("must not measure")))
    assert measure(metadata, output)["status"] == "cache_hit"
    assert valid_output(output, first["measurement_cache_key"])


def test_force_and_save_failures_preserve_old_valid_output(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "output"
    first = measure(metadata, output)
    old_metadata = Path(first["output_metadata_path"]).read_bytes()

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"):
            raise OSError("metadata failure")
        module._write_atomic(path, content)

    with pytest.raises(MeasureError):
        measure(metadata, output, force=True, writer=fail_metadata)
    assert Path(first["output_metadata_path"]).read_bytes() == old_metadata
    assert valid_output(output, first["measurement_cache_key"])


def test_data_save_failure_does_not_commit_metadata(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "output"
    with pytest.raises(MeasureError):
        measure(metadata, output, writer=lambda path, content: (_ for _ in ()).throw(OSError("data failure")))
    key = measurement_cache_key(load_source(metadata))
    assert not output_metadata_path(output, key).exists()


def test_valid_output_rejects_unsafe_metadata_reference(tmp_path):
    metadata, _ = source_files(tmp_path)
    result = measure(metadata, tmp_path / "output")
    meta_path = Path(result["output_metadata_path"])
    saved = json.loads(meta_path.read_text(encoding="utf-8"))
    saved["output_data_file"] = "../outside.jsonl"
    meta_path.write_text(json.dumps(saved), encoding="utf-8")
    assert not valid_output(tmp_path / "output", result["measurement_cache_key"])
