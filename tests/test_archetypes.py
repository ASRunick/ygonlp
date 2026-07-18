import hashlib
import json
from pathlib import Path

from ygonlp.archetypes import analyze_archetypes, build_archetype_profiles
from ygonlp.preprocess import preprocess


def record(card_id, archetype, text, card_type="Effect Monster"):
    return {"id": card_id, "name": f"Card {card_id}", "type": card_type, "frameType": "effect", "race": "Warrior",
            "archetype": archetype, "desc": text, "misc_info": [{"has_effect": 1, "tcg_date": "2020-01-01"}]}


def source(tmp_path: Path):
    raw = json.dumps({"data": [record(1, "Alpha", "Draw 1 card."), record(2, "Alpha", "Summon!", "Spell Card"),
                               record(3, "Beta", ""), record(4, None, "Ignored.")]}).encode()
    data = tmp_path / "raw.json"; data.write_bytes(raw)
    metadata = tmp_path / "raw.metadata.json"
    metadata.write_text(json.dumps({"schema_version": "1", "completed": True, "cache_key": "raw-key", "data_file": data.name,
                                    "data_sha256": hashlib.sha256(raw).hexdigest(), "record_count": 4}), encoding="utf-8")
    return preprocess(metadata, tmp_path / "preprocessed")["output_metadata_path"]


def test_profiles_metrics_missing_archetypes_distributions_and_order():
    result = build_archetype_profiles([
        {"archetype": "Beta", "text_normalized": "", "card_type": "Trap Card"},
        {"archetype": "Alpha", "text_normalized": "Draw 1 card.", "card_type": "Effect Monster"},
        {"archetype": None, "text_normalized": "Ignored.", "card_type": "Effect Monster"},
        {"archetype": "Alpha", "text_normalized": "Summon!", "card_type": "Spell Card"},
    ])
    assert result["missing_archetype_count"] == 1 and result["included_record_count"] == 3
    assert [profile["archetype"] for profile in result["archetypes"]] == ["Alpha", "Beta"]
    assert result["archetypes"][0] == {"archetype": "Alpha", "card_count": 2, "average_character_count": 9.5,
        "average_word_count": 2.0, "average_sentence_count": 1.0,
        "card_type_distribution": [{"card_type": "Effect Monster", "card_count": 1}, {"card_type": "Spell Card", "card_count": 1}]}


def test_archetype_output_cache_and_force_are_deterministic(tmp_path):
    metadata = source(tmp_path); output = tmp_path / "output"
    first = analyze_archetypes(metadata, output)
    contents = {name: path.read_bytes() for name, path in first["output_paths"].items()}
    assert analyze_archetypes(metadata, output)["status"] == "cache_hit"
    forced = analyze_archetypes(metadata, output, force=True)
    assert contents == {name: path.read_bytes() for name, path in forced["output_paths"].items()}
    saved = json.loads(first["output_metadata_path"].read_text(encoding="utf-8"))
    assert saved["missing_archetype_count"] == 1 and saved["included_record_count"] == 3
