import hashlib
import json
from pathlib import Path

import pytest

import ygonlp.price_analysis as module
from ygonlp.measure import measure
from ygonlp.preprocess import preprocess
from ygonlp.prices import snapshot_prices
from ygonlp.price_analysis import PriceAnalysisError, analyze_prices, parse_character_buckets


def card(card_id, text, price, *, card_type="Effect Monster", tcg_date="2020-01-01"):
    prices = [] if price is None else [{"cardmarket_price": str(price), "tcgplayer_price": str(price), "ebay_price": str(price), "amazon_price": str(price), "coolstuffinc_price": str(price)}]
    return {"id": card_id, "name": f"Card {card_id}", "type": card_type, "frameType": "effect", "race": "Warrior", "archetype": None,
            "desc": text, "misc_info": [{"has_effect": 1, "tcg_date": tcg_date}], "card_prices": prices}


def raw_source(tmp_path: Path, cards, *, fetched_at="2021-01-01T00:00:00Z"):
    tmp_path.mkdir(parents=True, exist_ok=True); raw = json.dumps({"data": cards}).encode(); data = tmp_path / "raw.json"; data.write_bytes(raw)
    metadata = tmp_path / "raw.metadata.json"
    metadata.write_text(json.dumps({"schema_version": "1", "completed": True, "cache_key": "raw-key", "data_file": data.name,
                                    "data_sha256": hashlib.sha256(raw).hexdigest(), "record_count": len(cards), "fetched_at": fetched_at}), encoding="utf-8")
    return metadata


def inputs(tmp_path: Path):
    price_cards = [card(1, "a" * 100, "10.00"), card(2, "b" * 200, "20.00", card_type="Spell Card"),
                   card(3, "c" * 300, "0.00", tcg_date="2030-01-01"), card(9, "unmatched", "0.00")]
    measurement_cards = [card(1, "a" * 100, None), card(2, "b" * 200, None, card_type="Spell Card"),
                         card(3, "c" * 300, None, tcg_date="2030-01-01"), card(4, "measurement only", None, tcg_date=None)]
    price_raw = raw_source(tmp_path / "price", price_cards); measurement_raw = raw_source(tmp_path / "measurement", measurement_cards)
    snapshot = snapshot_prices(price_raw, tmp_path / "snapshot")
    preprocessed = preprocess(measurement_raw, tmp_path / "preprocessed")
    measured = measure(preprocessed["output_metadata_path"], tmp_path / "measured")
    return snapshot["output_metadata_path"], measured["output_metadata_path"]


def test_exact_join_zero_policy_buckets_stats_correlations_and_dates(tmp_path):
    price_metadata, measurement_metadata = inputs(tmp_path)
    result = analyze_prices(price_metadata, measurement_metadata, tmp_path / "output", character_buckets="100,200")
    coverage = result["result"]["coverage"]
    assert coverage == {"total_snapshot_observation_count": 20, "joined_observation_count": 15, "unmatched_price_card_ids": [9],
                        "unmatched_measurement_card_ids": [4], "snapshot_zero_observation_count": 10, "joined_zero_observation_count": 5,
                        "analyzed_observation_count": 10, "excluded_observation_count": 10}
    assert coverage["snapshot_zero_observation_count"] == 10
    assert coverage["joined_zero_observation_count"] == 5
    assert coverage["unmatched_price_card_ids"] == [9]
    stats = next(item for item in result["result"]["statistics"] if item["vendor"] == "tcgplayer_price" and item["grouping"] == "overall")
    assert stats["statistics"]["count"] == 2 and stats["statistics"]["mean"] == 15.0 and stats["statistics"]["iqr"] == 5.0
    buckets = {item["group"] for item in result["result"]["statistics"] if item["grouping"] == "character_count_bucket"}
    assert buckets == {"0-100", "101-200"}
    years = {item["group"] for item in result["result"]["statistics"] if item["grouping"] == "tcg_year"}
    assert years == {"2020"}
    pearson = next(item for item in result["result"]["correlations"] if item["vendor"] == "tcgplayer_price" and item["metric"] == "character_count" and item["method"] == "pearson")
    assert pearson == {"vendor": "tcgplayer_price", "currency": "USD", "metric": "character_count", "method": "pearson", "status": "defined", "reason": None, "coefficient": 1.0}
    assert all(item["vendor"] != "cardmarket_price" or item["currency"] == "EUR" for item in result["result"]["statistics"])


def test_include_zero_undefined_correlations_and_bucket_validation(tmp_path):
    price_metadata, measurement_metadata = inputs(tmp_path)
    included = analyze_prices(price_metadata, measurement_metadata, tmp_path / "included", character_buckets="100,200", include_zero=True)
    stats = next(item for item in included["result"]["statistics"] if item["vendor"] == "tcgplayer_price" and item["grouping"] == "overall")
    assert stats["statistics"]["count"] == 3 and stats["statistics"]["mean"] == 10.0
    undefined = next(item for item in analyze_prices(price_metadata, measurement_metadata, tmp_path / "undefined", character_buckets="100")["result"]["correlations"] if item["metric"] == "word_count" and item["method"] == "pearson")
    assert undefined["status"] == "undefined" and undefined["coefficient"] is None
    for value in ("", "100,100", "200,100", "0,100", "a,100"):
        with pytest.raises(PriceAnalysisError): parse_character_buckets(value)


def test_cache_force_serialization_metadata_and_rollback(tmp_path):
    price_metadata, measurement_metadata = inputs(tmp_path); output = tmp_path / "output"
    first = analyze_prices(price_metadata, measurement_metadata, output)
    bytes_before = {name: path.read_bytes() for name, path in first["output_paths"].items()}; metadata_before = first["output_metadata_path"].read_bytes()
    assert analyze_prices(price_metadata, measurement_metadata, output)["status"] == "cache_hit"
    forced = analyze_prices(price_metadata, measurement_metadata, output, force=True)
    assert bytes_before == {name: path.read_bytes() for name, path in forced["output_paths"].items()} and metadata_before == forced["output_metadata_path"].read_bytes()
    metadata = json.loads(metadata_before)
    assert metadata["source_price_snapshot_cache_key"] and metadata["source_measurement_cache_key"] and metadata["zero_policy"]

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"): raise OSError("fail")
        module._write_atomic(path, content)
    with pytest.raises(PriceAnalysisError): analyze_prices(price_metadata, measurement_metadata, output, character_buckets="50,100", writer=fail_metadata)
    assert first["output_metadata_path"].read_bytes() == metadata_before
