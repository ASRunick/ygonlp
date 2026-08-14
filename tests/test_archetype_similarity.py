import hashlib
import json
from pathlib import Path

import pytest

import ygonlp.archetype_similarity as module
from ygonlp.archetype_similarity import ArchetypeSimilarityError, analyze_archetype_similarity, build_archetype_similarities
from ygonlp.preprocess import preprocess


def record(card_id, archetype, text, **changes):
    value = {"id": card_id, "name": f"Card {card_id}", "type": "Effect Monster", "frameType": "effect", "race": "Warrior", "archetype": archetype, "desc": text, "misc_info": [{"has_effect": 1, "tcg_date": "2020-01-01"}]}
    value.update(changes)
    return value


def source(tmp_path: Path) -> Path:
    raw = json.dumps({"data": [record(1, "Alpha", "draw one card"), record(2, "Alpha", "draw one card"), record(3, "Alpha", "draw two cards"), record(4, "Beta", "single card"), record(5, None, "ignored"), record(6, "Alpha", "spell text", type="Normal Monster", frameType="normal", misc_info=[{"has_effect": 0, "tcg_date": "2020-01-01"}]), record(7, "Alpha", ""), record(8, "Gamma", "normal text", type="Normal Monster", frameType="normal", misc_info=[{"has_effect": 0, "tcg_date": "2020-01-01"}])]}, ensure_ascii=False).encode()
    data = tmp_path / "raw.json"; data.write_bytes(raw)
    metadata = tmp_path / "raw.metadata.json"
    metadata.write_text(json.dumps({"schema_version": "1", "completed": True, "cache_key": "raw-key", "data_file": data.name, "data_sha256": hashlib.sha256(raw).hexdigest(), "record_count": 8}), encoding="utf-8")
    return preprocess(metadata, tmp_path / "preprocessed")["output_metadata_path"]


def test_pairs_are_deterministic_and_exclusions_are_recorded(tmp_path):
    metadata = source(tmp_path)
    records = [json.loads(line) for line in (metadata.parent / json.loads(metadata.read_text(encoding="utf-8"))["output_data_file"]).read_text(encoding="utf-8").splitlines()]
    result = build_archetype_similarities(records, top_n=2)
    assert result["missing_archetype_count"] == result["empty_text_count"] == 1
    assert result["excluded_non_target_count"] == result["insufficient_candidate_archetype_count"] == 2
    alpha = result["archetypes"][0]
    assert alpha["archetype"] == "Alpha" and alpha["candidate_card_count"] == 3
    assert [(match["left_card_id"], match["right_card_id"]) for match in alpha["matches"]] == [(1, 2), (1, 3)]
    assert alpha["matches"][0]["score"] == 1.0


def test_top_n_validation(tmp_path):
    with pytest.raises(ArchetypeSimilarityError, match="正の整数"):
        build_archetype_similarities([], top_n=True)


def test_output_cache_force_and_rollback_are_deterministic(tmp_path):
    metadata = source(tmp_path); output = tmp_path / "output"
    first = analyze_archetype_similarity(metadata, output, top_n=2)
    bytes_by_name = {name: path.read_bytes() for name, path in first["output_paths"].items()}
    assert analyze_archetype_similarity(metadata, output, top_n=2)["status"] == "cache_hit"
    forced = analyze_archetype_similarity(metadata, output, top_n=2, force=True)
    assert bytes_by_name == {name: path.read_bytes() for name, path in forced["output_paths"].items()}
    saved = json.loads(first["output_metadata_path"].read_text(encoding="utf-8"))
    assert saved["representation_identifier"] == module.REPRESENTATION_IDENTIFIER
    assert saved["top_n_per_archetype"] == 2

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"):
            raise OSError("metadata failure")
        module._write_atomic(path, content)

    files_before_failure = {path.name for path in output.iterdir()}
    with pytest.raises(ArchetypeSimilarityError, match="保存"):
        analyze_archetype_similarity(metadata, output, top_n=1, writer=fail_metadata)
    assert first["output_metadata_path"].read_bytes() == json.dumps(saved, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    assert {path.name for path in output.iterdir()} == files_before_failure
