import hashlib
import json
from pathlib import Path

import pytest

import ygonlp.prices as module
from ygonlp.prices import PriceSnapshotError, build_observations, snapshot_prices


def card(card_id, name="Card", prices=None):
    return {"id": card_id, "name": name, "card_prices": prices if prices is not None else [{
        "cardmarket_price": "1.20", "tcgplayer_price": "2.30", "ebay_price": "3.40",
        "amazon_price": "4.50", "coolstuffinc_price": "5.60",
    }]}


def source(tmp_path: Path, cards=None, fetched_at="2025-01-02T03:04:05Z"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"data": cards or [card(2, "Second"), card(1, "First", [{
        "cardmarket_price": "0.00", "tcgplayer_price": "2.00", "ebay_price": "3.00",
        "amazon_price": "4.00", "coolstuffinc_price": "5.00",
    }, {"cardmarket_price": "1.00", "tcgplayer_price": "1.50"}])]}).encode()
    data = tmp_path / "cards.json"; data.write_bytes(raw)
    metadata = tmp_path / "cards.metadata.json"
    metadata.write_text(json.dumps({"schema_version": "1", "completed": True, "cache_key": "collection-key",
                                    "data_file": data.name, "data_sha256": hashlib.sha256(raw).hexdigest(),
                                    "record_count": len(json.loads(raw)["data"]), "fetched_at": fetched_at}), encoding="utf-8")
    return metadata


def test_vendor_currency_mapping_zero_missing_and_deterministic_order(tmp_path):
    result = snapshot_prices(source(tmp_path), tmp_path / "output")
    rows = result["observations"]
    assert [(row["card_id"], row["vendor"]) for row in rows] == sorted((row["card_id"], row["vendor"]) for row in rows)
    zero = next(row for row in rows if row["card_id"] == 1 and row["vendor"] == "cardmarket_price")
    assert zero["currency"] == "EUR" and zero["raw_price"] == "0.00" and zero["decimal_price"] == "0.00" and zero["is_zero_price"] is True
    tcg = next(row for row in rows if row["card_id"] == 1 and row["vendor"] == "tcgplayer_price")
    assert tcg["currency"] == "USD" and tcg["raw_price"] == "1.50"
    metadata = json.loads(result["output_metadata_path"].read_text(encoding="utf-8"))
    assert metadata["missing_vendor_field_counts"]["ebay_price"] == 0
    assert metadata["zero_value_counts"]["cardmarket_price"] == 1
    assert metadata["source_collection_cache_key"] == "collection-key" and metadata["source_payload_checksum"]


def test_missing_vendor_counts_are_per_card_vendor_pair(tmp_path):
    available_later = card(1, prices=[
        {"cardmarket_price": "1.00"}, {"cardmarket_price": "2.00", "tcgplayer_price": "3.00"},
    ])
    result = snapshot_prices(source(tmp_path / "available", [available_later]), tmp_path / "available-output")
    metadata = json.loads(result["output_metadata_path"].read_text(encoding="utf-8"))
    assert any(row["vendor"] == "tcgplayer_price" for row in result["observations"])
    assert metadata["missing_vendor_field_counts"]["tcgplayer_price"] == 0
    assert metadata["missing_vendor_field_counts"]["ebay_price"] == 1

    empty = snapshot_prices(source(tmp_path / "empty", [card(2, prices=[])]), tmp_path / "empty-output")
    empty_metadata = json.loads(empty["output_metadata_path"].read_text(encoding="utf-8"))
    assert empty["observations"] == []
    assert empty_metadata["missing_vendor_field_counts"] == {vendor: 1 for vendor in sorted(module.VENDORS)}

    absent_everywhere = snapshot_prices(source(tmp_path / "absent", [card(3, prices=[{"cardmarket_price": "1"}, {"cardmarket_price": "2"}])]), tmp_path / "absent-output")
    absent_metadata = json.loads(absent_everywhere["output_metadata_path"].read_text(encoding="utf-8"))
    assert absent_metadata["missing_vendor_field_counts"]["tcgplayer_price"] == 1


@pytest.mark.parametrize("value", ["oops", "-1.00", "NaN", "Infinity", 1.0])
def test_invalid_prices_rejected(tmp_path, value):
    prices = [{"cardmarket_price": value, "tcgplayer_price": "1", "ebay_price": "1", "amazon_price": "1", "coolstuffinc_price": "1"}]
    with pytest.raises(PriceSnapshotError): snapshot_prices(source(tmp_path, [card(1, prices=prices)]), tmp_path / "output")


def test_unknown_vendor_duplicate_id_and_fetched_at_rejected(tmp_path):
    with pytest.raises(PriceSnapshotError, match="unknown"):
        snapshot_prices(source(tmp_path, [card(1, prices=[{"unexpected_price": "1"}])]), tmp_path / "output")
    with pytest.raises(PriceSnapshotError, match="duplicate"):
        snapshot_prices(source(tmp_path, [card(1), card(1)]), tmp_path / "output")
    with pytest.raises(PriceSnapshotError, match="fetched_at"):
        snapshot_prices(source(tmp_path, fetched_at="not-a-timestamp"), tmp_path / "output")
    with pytest.raises(PriceSnapshotError, match="UTC"):
        snapshot_prices(source(tmp_path, fetched_at="2025-01-02T03:04:05+09:00"), tmp_path / "output")


def test_cache_force_byte_identical_and_rollback(tmp_path):
    input_metadata = source(tmp_path); output = tmp_path / "output"
    first = snapshot_prices(input_metadata, output)
    data_bytes = first["output_data_path"].read_bytes(); metadata_bytes = first["output_metadata_path"].read_bytes()
    assert snapshot_prices(input_metadata, output)["status"] == "cache_hit"
    forced = snapshot_prices(input_metadata, output, force=True)
    assert forced["output_data_path"].read_bytes() == data_bytes
    assert forced["output_metadata_path"].read_bytes() == metadata_bytes

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"): raise OSError("fail")
        module._write_bytes_atomic(path, content)
    changed = source(tmp_path / "changed", [card(3)], fetched_at="2025-01-03T00:00:00Z")
    with pytest.raises(PriceSnapshotError): snapshot_prices(changed, output, writer=fail_metadata)
    assert first["output_metadata_path"].read_bytes() == metadata_bytes
