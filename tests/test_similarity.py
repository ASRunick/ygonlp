import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

import ygonlp.similarity as module
from ygonlp.preprocess import preprocess
from ygonlp.similarity import SimilarityError, search_records, search_similar, valid_output


def record(card_id: int, name: str, text: str, **changes):
    value = {
        "id": card_id, "name": name, "type": "Effect Monster", "frameType": "effect",
        "race": "Warrior", "archetype": None, "desc": text,
        "misc_info": [{"has_effect": 1, "tcg_date": "2020-01-01", "ocg_date": "2019-01-01"}],
    }
    value.update(changes)
    return value


def source(tmp_path: Path, records: list[dict]) -> Path:
    raw = json.dumps({"data": records}, ensure_ascii=False).encode("utf-8")
    data = tmp_path / "raw.json"
    data.write_bytes(raw)
    metadata = tmp_path / "raw.metadata.json"
    metadata.write_text(json.dumps({
        "schema_version": "1", "completed": True, "cache_key": "source-key", "data_file": data.name,
        "data_sha256": hashlib.sha256(raw).hexdigest(), "record_count": len(records),
    }), encoding="utf-8")
    return preprocess(metadata, tmp_path / "preprocessed")["output_metadata_path"]


def normalized(tmp_path: Path, records: list[dict]) -> list[dict]:
    metadata = source(tmp_path, records)
    saved = json.loads(Path(metadata).read_text(encoding="utf-8"))
    return [json.loads(line) for line in (Path(metadata).parent / saved["output_data_file"]).read_text(encoding="utf-8").splitlines()]


def cards():
    return [
        record(1, "Query", "draw one card from deck"),
        record(2, "Tie A", "draw one card from deck"),
        record(3, "Tie B", "draw one card from deck"),
        record(4, "Partial", "draw two cards"),
        record(5, "Blank", ""),
        record(6, "Future", "draw one card", misc_info=[{"has_effect": 1, "tcg_date": "2030-01-01"}]),
        record(7, "Missing", "draw one card", misc_info=[{"has_effect": 1, "tcg_date": None}]),
        record(8, "Spell", "draw one card", type="Spell Card", frameType="spell"),
    ]


def test_deterministic_ranking_tie_and_duplicate_text(tmp_path):
    rows = normalized(tmp_path, cards())
    result = search_records(rows, card_id=1, top_n=4, today=date(2021, 1, 1))
    assert [item["card_id"] for item in result] == [2, 3, 6, 7]
    assert result[0]["score"] == result[1]["score"] == 1.0


def test_raw_score_ranking_precedes_six_decimal_rounding(tmp_path):
    repeated = "alpha " * 1000
    rows = normalized(tmp_path, [
        record(1, "Query", repeated + "beta"),
        record(2, "Lower raw score", repeated + "gamma"),
        record(3, "Higher raw score", "alpha " * 1001 + "gamma"),
    ])
    result = search_records(rows, card_id=1, top_n=2, today=date(2021, 1, 1))
    assert [item["card_id"] for item in result] == [3, 2]
    assert result[0]["score"] == result[1]["score"] == 0.999998


def test_empty_query_and_tokenless_query_are_errors(tmp_path):
    rows = normalized(tmp_path, cards())
    with pytest.raises(SimilarityError, match="空"):
        search_records(rows, card_id=5, today=date(2021, 1, 1))
    rows[0]["text_normalized"] = "!!!"
    with pytest.raises(SimilarityError, match="token"):
        search_records(rows, card_id=1, today=date(2021, 1, 1))


def test_ambiguous_name_and_query_resolution_over_all_records(tmp_path):
    rows = normalized(tmp_path, cards() + [record(9, "Query", "different")])
    with pytest.raises(SimilarityError, match="複数"):
        search_records(rows, name="Query", today=date(2021, 1, 1))
    assert search_records(rows, card_id=1, top_n=1, today=date(2021, 1, 1))[0]["card_id"] == 2


def test_card_type_release_status_and_top_n_filters(tmp_path):
    rows = normalized(tmp_path, cards())
    assert [item["card_id"] for item in search_records(rows, card_id=1, card_type="Spell Card", today=date(2021, 1, 1))] == [8]
    assert [item["card_id"] for item in search_records(rows, card_id=1, release_status="future_dated", today=date(2021, 1, 1))] == [6]
    assert [item["card_id"] for item in search_records(rows, card_id=1, release_status="missing_date", today=date(2021, 1, 1))] == [7]
    for value in (0, -1, True):
        with pytest.raises(SimilarityError, match="正の整数"):
            search_records(rows, card_id=1, top_n=value, today=date(2021, 1, 1))


def test_cache_force_output_determinism_metadata_and_rollback(tmp_path):
    metadata = source(tmp_path, cards())
    output = tmp_path / "output"
    first = search_similar(metadata, output, card_id=1, top_n=3, today=date(2021, 1, 1))
    first_bytes = {name: path.read_bytes() for name, path in first["output_paths"].items()}
    metadata_bytes = Path(first["output_metadata_path"]).read_bytes()
    assert search_similar(metadata, output, card_id=1, top_n=3, today=date(2021, 1, 1))["status"] == "cache_hit"
    forced = search_similar(metadata, output, card_id=1, top_n=3, today=date(2021, 1, 1), force=True)
    assert first_bytes == {name: path.read_bytes() for name, path in forced["output_paths"].items()}
    assert metadata_bytes == Path(forced["output_metadata_path"]).read_bytes()
    saved = json.loads(metadata_bytes)
    assert saved["sklearn_version"] == module.sklearn.__version__
    assert saved["vectorizer_class"] == "sklearn.feature_extraction.text.TfidfVectorizer"
    assert saved["vectorizer_parameters"]["dtype"] == "float64"
    assert valid_output(output, first["similarity_cache_key"], first["source"], module._key_payload(first["source"], card_id=1, name=None, top_n=3, card_type=None, release_status=None, today=date(2021, 1, 1)), first["result"])

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"):
            raise OSError("metadata failure")
        module._write_atomic(path, content)

    with pytest.raises(SimilarityError, match="保存"):
        search_similar(metadata, output, card_id=1, top_n=2, today=date(2021, 1, 1), writer=fail_metadata)
    assert Path(first["output_metadata_path"]).read_bytes() == metadata_bytes
