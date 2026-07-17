import hashlib
import json
from pathlib import Path

import pytest

import ygonlp.preprocess as module
from ygonlp.preprocess import (
    RECORD_FIELDS,
    PreprocessError,
    cleanup_preprocess,
    dry_run_lines,
    load_source,
    output_metadata_path,
    preprocess,
    preprocessing_cache_key,
    referenced_preprocess_generation_names,
    serialize_jsonl,
    transform_cards,
    unreferenced_preprocess_generation_names,
    valid_output,
    validate_preprocessed_record,
    verify_preprocessed_cache,
)


def card(card_id=1, **changes):
    value = {
        "id": card_id, "name": f"Card {card_id}", "type": "Effect Monster", "frameType": "effect",
        "race": "Warrior", "archetype": "Example", "desc": "  Effect\r\nText; (test)  ",
        "misc_info": [{"has_effect": 1, "tcg_date": "2020-01-02", "ocg_date": "2019-12-01"}],
    }
    value.update(changes)
    return value


def source_files(tmp_path: Path, cards=None, *, metadata_changes=None):
    cards = cards or [card()]
    payload = {"data": cards}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    data_path = tmp_path / "cards-source.json"
    data_path.write_bytes(raw)
    metadata = {
        "schema_version": "1", "completed": True, "cache_key": "source-cache-key",
        "data_file": data_path.name, "data_sha256": hashlib.sha256(raw).hexdigest(), "record_count": len(cards),
    }
    metadata.update(metadata_changes or {})
    metadata_path = tmp_path / "cards-source.metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return metadata_path, data_path


def test_load_source_and_dry_run_are_read_only(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "new-output"
    plan = dry_run_lines(metadata, output)
    assert any(line.startswith("input metadata path:") for line in plan)
    assert any(line.startswith("warning counts:") for line in plan)
    assert not output.exists()


@pytest.mark.parametrize("change", [
    {"schema_version": "wrong"}, {"completed": False}, {"data_file": "../outside.json"},
    {"data_file": "C:/outside.json"}, {"data_file": None}, {"data_sha256": "0" * 64}, {"record_count": 2},
])
def test_raw_metadata_validation_failures(tmp_path, change):
    metadata, _ = source_files(tmp_path, metadata_changes=change)
    with pytest.raises(PreprocessError):
        load_source(metadata)


@pytest.mark.parametrize("payload", [b"{", json.dumps({}).encode(), json.dumps({"data": {}}).encode(), json.dumps({"data": []}).encode()])
def test_raw_data_structure_failures(tmp_path, payload):
    metadata, data = source_files(tmp_path)
    data.write_bytes(payload)
    meta = json.loads(metadata.read_text(encoding="utf-8"))
    meta["data_sha256"] = hashlib.sha256(payload).hexdigest()
    metadata.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(PreprocessError):
        load_source(metadata)


def test_transform_preserves_raw_and_normalizes_only_allowed_text():
    records, warnings, duplicates = transform_cards([card()])
    record = records[0]
    assert record["text_raw"] == "  Effect\r\nText; (test)  "
    assert record["text_normalized"] == "Effect\nText; (test)"
    assert record["is_effect_text_target"] is True
    assert warnings == {} and duplicates == 0


@pytest.mark.parametrize("kind, expected_target, reason", [
    (card(type="Spell Card", frameType="spell", misc_info=[{}]), True, None),
    (card(type="Trap Card", frameType="trap", misc_info=[{}]), True, None),
    (card(type="Normal Monster", frameType="normal", misc_info=[{"has_effect": 0}]), False, "normal_monster_flavor_text"),
    (card(type="Token", frameType="token", misc_info=[{}]), False, "token"),
    (card(type="Skill Card", frameType="skill", misc_info=[{}]), False, "skill_card"),
    (card(type="Pendulum Effect Monster", frameType="effect_pendulum"), True, None),
    (card(type="Uncatalogued Card", frameType="other", misc_info=[{}]), False, "unknown_card_type"),
])
def test_explicit_target_policy(kind, expected_target, reason):
    records, warnings, _ = transform_cards([kind])
    assert records[0]["is_effect_text_target"] is expected_target
    assert records[0]["exclusion_reason"] == reason
    if reason == "unknown_card_type":
        assert warnings["unknown_card_type"] == 1


@pytest.mark.parametrize("desc, normalized, reason", [
    (None, None, "missing_text"), ("", "", "empty_text"), ("\rA\r\nB\n", "A\nB", None),
])
def test_text_missing_empty_and_line_endings(desc, normalized, reason):
    records, _, _ = transform_cards([card(desc=desc)])
    assert records[0]["text_normalized"] == normalized
    assert records[0]["exclusion_reason"] == reason


@pytest.mark.parametrize("value", [None, "", "2020-13-01", "2020-02-30"])
def test_dates_missing_and_invalid_become_null_with_warning(value):
    records, warnings, _ = transform_cards([card(misc_info=[{"has_effect": 1, "tcg_date": value, "ocg_date": value}])])
    assert records[0]["tcg_date"] is None and records[0]["ocg_date"] is None
    assert sum(warnings.values()) >= 2


def test_multiple_misc_info_identical_and_compatible_dates():
    value = {"has_effect": 1, "tcg_date": "2020-01-02", "ocg_date": "2019-12-01"}
    records, _, _ = transform_cards([card(misc_info=[value, dict(value)])])
    assert records[0]["tcg_date"] == "2020-01-02"
    compatible = [{"has_effect": 1, "tcg_date": "2020-01-02"}, {"has_effect": 1, "ocg_date": "2019-12-01"}]
    records, _, _ = transform_cards([card(misc_info=compatible)])
    assert records[0]["ocg_date"] == "2019-12-01"


def test_conflicting_misc_info_is_fatal():
    values = [{"has_effect": 1, "tcg_date": "2020-01-02"}, {"has_effect": 1, "tcg_date": "2020-01-03"}]
    with pytest.raises(PreprocessError):
        transform_cards([card(misc_info=values)])


def test_empty_misc_info_is_missing_dates_warning():
    records, warnings, _ = transform_cards([card(misc_info=[])])
    assert records[0]["tcg_date"] is None and records[0]["ocg_date"] is None
    assert warnings["missing_tcg_date"] == 1 and warnings["missing_ocg_date"] == 1


def test_duplicate_ids_deduplicate_or_fail():
    records, warnings, duplicates = transform_cards([card(2), card(1), card(2)])
    assert [record["card_id"] for record in records] == [1, 2]
    assert duplicates == 1 and warnings["duplicate_card_id"] == 1
    changed = card(2, name="different")
    with pytest.raises(PreprocessError):
        transform_cards([card(2), changed])


def test_jsonl_is_deterministic_utf8_lf_and_fixed_key_order():
    records, _, _ = transform_cards([card(2, name="非ASCII"), card(1)])
    first = serialize_jsonl(records)
    second = serialize_jsonl(records)
    assert first == second and first.endswith(b"\n") and b"\r" not in first and not first.startswith(b"\xef\xbb\xbf")
    assert "非ASCII".encode("utf-8") in first
    lines = first.decode("utf-8").splitlines()
    assert len(lines) == 2
    assert list(json.loads(lines[0])) == list(RECORD_FIELDS)


def test_preprocess_save_metadata_checksum_cache_hit_and_force(tmp_path):
    metadata, _ = source_files(tmp_path, [card(2), card(1)])
    output = tmp_path / "out"
    first = preprocess(metadata, output)
    assert first["status"] == "processed"
    source = load_source(metadata)
    key = preprocessing_cache_key(source)
    meta_path = output_metadata_path(output, key)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    data = output / meta["output_data_file"]
    assert valid_output(output, key)
    assert hashlib.sha256(data.read_bytes()).hexdigest() == meta["output_sha256"]
    assert meta["output_record_count"] == 2
    hit = preprocess(metadata, output)
    assert hit["status"] == "cache_hit"
    forced = preprocess(metadata, output, force=True)
    assert forced["status"] == "processed"


def test_force_dry_run_creates_no_output(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "missing-output"
    plan = preprocess(metadata, output, force=True, dry_run=True)
    assert plan["cache_hit"] is False
    assert not output.exists()


def test_cache_hit_skips_record_transformation(tmp_path, monkeypatch):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"
    first = preprocess(metadata, output)

    def should_not_transform(_):
        raise AssertionError("cache hit must not transform records")

    monkeypatch.setattr(module, "transform_cards", should_not_transform)
    hit = preprocess(metadata, output)
    assert hit["status"] == "cache_hit"
    assert hit["warnings"] == {} and hit["duplicates"] == 0
    assert valid_output(output, first["preprocessing_cache_key"])


def test_metadata_write_failure_preserves_existing_output(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"
    first = preprocess(metadata, output)
    old_metadata = Path(first["output_metadata_path"]).read_bytes()
    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"):
            raise OSError("metadata failure")
        module._write_atomic(path, content)
    with pytest.raises(PreprocessError):
        preprocess(metadata, output, force=True, writer=fail_metadata)
    assert Path(first["output_metadata_path"]).read_bytes() == old_metadata
    assert valid_output(output, first["preprocessing_cache_key"])


def test_metadata_replace_failure_preserves_old_generation_and_cleans_temporary(tmp_path, monkeypatch):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"
    first = preprocess(metadata, output)
    metadata_path = Path(first["output_metadata_path"])
    old_metadata = metadata_path.read_bytes()
    old_data = Path(first["output_data_path"])
    real_replace = module.os.replace

    def fail_metadata_replace(source, destination):
        if Path(destination) == metadata_path:
            raise OSError("metadata replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_metadata_replace)
    with pytest.raises(PreprocessError) as exc:
        preprocess(metadata, output, force=True)

    assert isinstance(exc.value.__cause__, OSError)
    assert "metadata replace failure" in str(exc.value.__cause__)
    assert metadata_path.read_bytes() == old_metadata
    assert old_data.exists() and valid_output(output, first["preprocessing_cache_key"])
    assert list(output.glob("*.tmp")) == []
    assert len(list(output.glob("*.jsonl"))) == 1


def test_cleanup_failure_does_not_mask_metadata_save_failure(tmp_path, monkeypatch):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"
    real_unlink = Path.unlink

    def fail_generation_cleanup(path, *args, **kwargs):
        if path.suffix == ".jsonl":
            raise OSError("cleanup failure")
        return real_unlink(path, *args, **kwargs)

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"):
            raise OSError("root metadata failure")
        module._write_atomic(path, content)

    monkeypatch.setattr(Path, "unlink", fail_generation_cleanup)
    with pytest.raises(PreprocessError) as exc:
        preprocess(metadata, output, writer=fail_metadata)

    assert isinstance(exc.value.__cause__, OSError)
    assert str(exc.value.__cause__) == "root metadata failure"


def test_jsonl_write_failure_does_not_commit_metadata(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"

    def fail_data(path, content):
        raise OSError("data failure")

    with pytest.raises(PreprocessError):
        preprocess(metadata, output, writer=fail_data)
    assert not output_metadata_path(output, preprocessing_cache_key(load_source(metadata))).exists()


def test_output_metadata_path_traversal_is_invalid(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"
    result = preprocess(metadata, output)
    meta_path = Path(result["output_metadata_path"])
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["output_data_file"] = "../outside.jsonl"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert not valid_output(output, result["preprocessing_cache_key"])


def test_cleanup_detects_unreferenced_generation_as_dry_run(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"
    result = preprocess(metadata, output)
    active = Path(result["output_data_path"])
    orphan = output / "cards-normalized-0123456789abcdef-fedcba9876543210.jsonl"
    orphan.write_text("orphan\n", encoding="utf-8")

    plan = cleanup_preprocess(output)

    assert plan["candidates"] == [orphan]
    assert plan["deleted"] == []
    assert orphan.exists() and active.exists()


def test_cleanup_delete_removes_only_unreferenced_generation_and_not_metadata(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"
    result = preprocess(metadata, output)
    active = Path(result["output_data_path"])
    metadata_path = Path(result["output_metadata_path"])
    orphan = output / "cards-normalized-0123456789abcdef-fedcba9876543210.jsonl"
    orphan.write_text("orphan\n", encoding="utf-8")

    cleaned = cleanup_preprocess(output, delete=True)

    assert cleaned["deleted"] == [orphan]
    assert not orphan.exists()
    assert active.exists() and metadata_path.exists()


def test_cleanup_ignores_invalid_names_directories_and_symlinks(tmp_path):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"
    result = preprocess(metadata, output)
    active = Path(result["output_data_path"])
    invalid = output / "cards-normalized-not-a-generation.jsonl"
    invalid.write_text("keep", encoding="utf-8")
    directory = output / "cards-normalized-1111111111111111-2222222222222222.jsonl"
    directory.mkdir()
    outside = tmp_path / "cards-normalized-3333333333333333-4444444444444444.jsonl"
    outside.write_text("outside", encoding="utf-8")
    link = output / "cards-normalized-5555555555555555-6666666666666666.jsonl"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic link を作成できない環境")

    cleaned = cleanup_preprocess(output, delete=True)

    assert cleaned["deleted"] == []
    assert active.exists() and invalid.exists() and directory.exists() and link.is_symlink()
    assert outside.exists()


@pytest.mark.parametrize("changes", [
    {"completed": False},
    {"metadata_schema_version": 0},
    {"record_schema_version": 0},
    {"output_format": "json"},
    {"output_data_file": "../outside.jsonl"},
])
def test_cleanup_rejects_invalid_metadata_without_deleting(tmp_path, changes):
    metadata, _ = source_files(tmp_path)
    output = tmp_path / "out"
    result = preprocess(metadata, output)
    active = Path(result["output_data_path"])
    metadata_path = Path(result["output_metadata_path"])
    orphan = output / "cards-normalized-0123456789abcdef-fedcba9876543210.jsonl"
    orphan.write_text("orphan\n", encoding="utf-8")
    output_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    output_metadata.update(changes)
    metadata_path.write_text(json.dumps(output_metadata), encoding="utf-8")

    with pytest.raises(PreprocessError):
        cleanup_preprocess(output, delete=True)

    assert active.exists() and orphan.exists() and metadata_path.exists()


def test_cleanup_handles_no_candidates_and_missing_output(tmp_path):
    assert cleanup_preprocess(tmp_path / "missing") == {"candidates": [], "deleted": []}
    output = tmp_path / "out"
    metadata, _ = source_files(tmp_path)
    preprocess(metadata, output)
    assert cleanup_preprocess(output) == {"candidates": [], "deleted": []}


def test_cleanup_pure_selection_only_accepts_generation_names():
    active = "cards-normalized-0123456789abcdef-fedcba9876543210.jsonl"
    orphan = "cards-normalized-1111111111111111-2222222222222222.jsonl"
    metadata = {
        "metadata_schema_version": 1,
        "completed": True,
        "record_schema_version": 1,
        "output_format": "jsonl",
        "sort_order": "card_id_ascending",
        "preprocessing_cache_key": "cache-key",
        "output_data_file": active,
    }
    referenced = referenced_preprocess_generation_names([metadata])

    assert referenced == {active}
    assert unreferenced_preprocess_generation_names([active, orphan, "other.jsonl"], referenced) == [orphan]

def test_deep_verify_accepts_valid_preprocessing_cache(tmp_path):
    metadata, _ = source_files(tmp_path, [card(2), card(1)])
    result = preprocess(metadata, tmp_path / "out")

    verified = verify_preprocessed_cache(Path(result["output_metadata_path"]))

    assert verified["status"] == "valid"
    assert verified["record_count"] == 2
    assert verified["data_path"] == Path(result["output_data_path"])

def rewrite_preprocessed_data(
    metadata_path: Path,
    records: list[dict],
) -> Path:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    data_path = metadata_path.parent / metadata["output_data_file"]
    content = (
        "\n".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            for record in records
        )
        + "\n"
    ).encode("utf-8")
    data_path.write_bytes(content)
    metadata["output_sha256"] = hashlib.sha256(content).hexdigest()
    metadata["output_record_count"] = len(records)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return data_path

def test_deep_verify_rejects_wrong_key_order(tmp_path):
    raw_metadata, _ = source_files(tmp_path)
    result = preprocess(raw_metadata, tmp_path / "out")
    metadata_path = Path(result["output_metadata_path"])
    data_path = Path(result["output_data_path"])

    record = json.loads(data_path.read_text(encoding="utf-8"))
    reordered = {"card_id": record["card_id"], **record}
    rewrite_preprocessed_data(metadata_path, [reordered])

    with pytest.raises(PreprocessError, match="キー順"):
        verify_preprocessed_cache(metadata_path)

@pytest.mark.parametrize(
    "card_ids",
    [
        [2, 1],
        [1, 1],
    ],
)
def test_deep_verify_rejects_card_id_order_or_duplicates(tmp_path, card_ids):
    raw_metadata, _ = source_files(tmp_path, [card(1), card(2)])
    result = preprocess(raw_metadata, tmp_path / "out")
    metadata_path = Path(result["output_metadata_path"])
    data_path = Path(result["output_data_path"])
    records = [
        json.loads(line)
        for line in data_path.read_text(encoding="utf-8").splitlines()
    ]

    for record, card_id in zip(records, card_ids):
        record["card_id"] = card_id

    rewrite_preprocessed_data(metadata_path, records)

    with pytest.raises(PreprocessError, match="順序または一意性"):
        verify_preprocessed_cache(metadata_path)

def test_deep_verify_rejects_invalid_record_field_type(tmp_path):
    raw_metadata, _ = source_files(tmp_path)
    result = preprocess(raw_metadata, tmp_path / "out")
    metadata_path = Path(result["output_metadata_path"])
    data_path = Path(result["output_data_path"])
    record = json.loads(data_path.read_text(encoding="utf-8"))
    record["has_text"] = 1

    rewrite_preprocessed_data(metadata_path, [record])

    with pytest.raises(PreprocessError, match="boolean"):
        verify_preprocessed_cache(metadata_path)

def test_deep_verify_rejects_metadata_record_count_mismatch(tmp_path):
    raw_metadata, _ = source_files(tmp_path)
    result = preprocess(raw_metadata, tmp_path / "out")
    metadata_path = Path(result["output_metadata_path"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["output_record_count"] = 2
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(PreprocessError, match="record count"):
        verify_preprocessed_cache(metadata_path)


def test_deep_verify_rejects_invalid_jsonl(tmp_path):
    raw_metadata, _ = source_files(tmp_path)
    result = preprocess(raw_metadata, tmp_path / "out")
    metadata_path = Path(result["output_metadata_path"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    data_path = metadata_path.parent / metadata["output_data_file"]

    content = b"{invalid-json}\n"
    data_path.write_bytes(content)
    metadata["output_sha256"] = hashlib.sha256(content).hexdigest()
    metadata["output_record_count"] = 1
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(PreprocessError, match="parse"):
        verify_preprocessed_cache(metadata_path)
