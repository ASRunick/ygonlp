"""固定collection入力から主要な分析出力までを結ぶE2Eスモークテスト。"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from ygonlp.measure import measure
from ygonlp.preprocess import preprocess, verify_preprocessed_cache
from ygonlp.price_analysis import analyze_prices
from ygonlp.prices import snapshot_prices
from ygonlp.summarize import summarize
from ygonlp.timeseries import analyze_timeseries


def _prices(*, cardmarket: str, tcgplayer: str | None = "2.00", ebay: str | None = "3.00",
            amazon: str | None = "4.00", coolstuffinc: str | None = "5.00") -> list[dict[str, str]]:
    values = {
        "cardmarket_price": cardmarket,
        "tcgplayer_price": tcgplayer,
        "ebay_price": ebay,
        "amazon_price": amazon,
        "coolstuffinc_price": coolstuffinc,
    }
    return [{name: value for name, value in values.items() if value is not None}]


def _card(card_id: int, *, name: str, card_type: str, frame_type: str, text: str | None,
          has_effect: int, tcg_date: str | None, prices: list[dict[str, str]]) -> dict[str, object]:
    return {
        "id": card_id,
        "name": name,
        "type": card_type,
        "frameType": frame_type,
        "race": "Warrior",
        "archetype": None,
        "desc": text,
        "misc_info": [{"has_effect": has_effect, "tcg_date": tcg_date}],
        "card_prices": prices,
    }


def _collection_metadata(tmp_path: Path) -> Path:
    cards = [
        _card(1, name="Alpha", card_type="Effect Monster", frame_type="effect", text="Draw 1 card.",
              has_effect=1, tcg_date="2020-01-01", prices=_prices(cardmarket="0.00", tcgplayer="2.00")),
        _card(2, name="Beta", card_type="Spell Card", frame_type="spell", text="Draw 1 card.",
              has_effect=1, tcg_date="2021-01-01", prices=_prices(cardmarket="1.00", tcgplayer="4.00")),
        _card(3, name="Flavor", card_type="Normal Monster", frame_type="normal", text="Flavor text.",
              has_effect=0, tcg_date="2020-02-01", prices=_prices(cardmarket="3.00")),
        _card(4, name="Missing", card_type="Effect Monster", frame_type="effect", text=None,
              has_effect=1, tcg_date="2021-02-01", prices=_prices(cardmarket="4.00")),
        _card(5, name="Unknown date", card_type="Effect Monster", frame_type="effect", text="Duplicate effect.",
              has_effect=1, tcg_date=None,
              prices=_prices(cardmarket="0.00", tcgplayer=None, ebay=None, amazon=None, coolstuffinc=None)),
    ]
    payload = json.dumps({"data": cards}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    data_path = tmp_path / "cards.json"
    data_path.write_bytes(payload)
    metadata_path = tmp_path / "cards.metadata.json"
    metadata_path.write_text(json.dumps({
        "schema_version": "1",
        "completed": True,
        "cache_key": "e2e-fixed-collection-v1",
        "data_file": data_path.name,
        "data_sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": len(cards),
        "fetched_at": "2021-12-31T00:00:00Z",
    }), encoding="utf-8")
    return metadata_path


def test_fixed_collection_pipeline_metadata_contracts_and_cache_hits(tmp_path: Path) -> None:
    collection_metadata = _collection_metadata(tmp_path)

    preprocessed = preprocess(collection_metadata, tmp_path / "preprocessed")
    verified = verify_preprocessed_cache(preprocessed["output_metadata_path"])
    measured = measure(preprocessed["output_metadata_path"], tmp_path / "measured")
    summary = summarize(measured["output_metadata_path"], tmp_path / "summary")
    timeseries = analyze_timeseries(measured["output_metadata_path"], tmp_path / "timeseries", today=date(2021, 12, 31))
    prices = snapshot_prices(collection_metadata, tmp_path / "prices")
    price_analysis = analyze_prices(
        prices["output_metadata_path"], measured["output_metadata_path"], tmp_path / "price-analysis",
        character_buckets="10,20",
    )
    measurement_metadata = json.loads(measured["output_metadata_path"].read_text(encoding="utf-8"))
    summary_metadata = json.loads(summary["output_metadata_path"].read_text(encoding="utf-8"))
    price_snapshot_metadata = json.loads(prices["output_metadata_path"].read_text(encoding="utf-8"))
    price_analysis_metadata = json.loads(price_analysis["output_metadata_path"].read_text(encoding="utf-8"))
    artifacts = [
        preprocessed["output_data_path"], preprocessed["output_metadata_path"],
        measured["output_data_path"], measured["output_metadata_path"],
        prices["output_data_path"], prices["output_metadata_path"],
        *summary["output_paths"].values(), summary["output_metadata_path"],
        *timeseries["output_paths"].values(), timeseries["output_metadata_path"],
        *price_analysis["output_paths"].values(), price_analysis["output_metadata_path"],
    ]
    artifact_bytes = {path: path.read_bytes() for path in artifacts}

    assert verified["status"] == "valid"
    assert measurement_metadata["source_preprocessing_cache_key"] == preprocessed["preprocessing_cache_key"]
    assert measurement_metadata["measured_record_count"] == 3
    assert measurement_metadata["excluded_record_count"] == 2
    assert summary_metadata["source_measurement_cache_key"] == measured["measurement_cache_key"]
    assert price_analysis_metadata["source_price_snapshot_cache_key"] == prices["price_snapshot_cache_key"]
    assert price_analysis_metadata["source_measurement_cache_key"] == measured["measurement_cache_key"]
    assert timeseries["result"]["included_record_count"] == 2
    assert timeseries["result"]["missing_date_count"] == 1
    assert len(prices["observations"]) == 21
    assert prices["observations"][0]["card_id"] == 1
    assert price_snapshot_metadata["zero_value_counts"]["cardmarket_price"] == 2
    assert price_snapshot_metadata["missing_vendor_field_counts"]["ebay_price"] == 1
    assert price_analysis["result"]["coverage"]["unmatched_price_card_ids"] == [3, 4]
    assert price_analysis["result"]["coverage"]["snapshot_zero_observation_count"] == 2
    assert all(path.is_file() for path in summary["output_paths"].values())
    assert all(path.is_file() for path in timeseries["output_paths"].values())
    assert all(path.is_file() for path in price_analysis["output_paths"].values())

    assert preprocess(collection_metadata, tmp_path / "preprocessed")["status"] == "cache_hit"
    assert measure(preprocessed["output_metadata_path"], tmp_path / "measured")["status"] == "cache_hit"
    assert summarize(measured["output_metadata_path"], tmp_path / "summary")["status"] == "cache_hit"
    assert analyze_timeseries(measured["output_metadata_path"], tmp_path / "timeseries", today=date(2021, 12, 31))["status"] == "cache_hit"
    assert snapshot_prices(collection_metadata, tmp_path / "prices")["status"] == "cache_hit"
    assert analyze_prices(
        prices["output_metadata_path"], measured["output_metadata_path"], tmp_path / "price-analysis",
        character_buckets="10,20",
    )["status"] == "cache_hit"
    assert {path: path.read_bytes() for path in artifacts} == artifact_bytes
