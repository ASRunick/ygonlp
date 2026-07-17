"""検証済みcollection出力からの決定論的vendor価格snapshot。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from .collect import _write_bytes_atomic
from .preprocess import Source, _safe_child, load_source

PRICE_SNAPSHOT_METADATA_SCHEMA_VERSION = 1
PRICE_OBSERVATION_SCHEMA_VERSION = 1
PRICE_SNAPSHOT_IDENTIFIER = "card_level_vendor_minimum_across_versions_v1"
ORDERING_IDENTIFIER = "snapshot_timestamp_ascending_card_id_ascending_vendor_ascending_v1"
KEY_PREFIX_LENGTH = CONTENT_PREFIX_LENGTH = 16
VENDORS = {
    "cardmarket_price": "EUR", "tcgplayer_price": "USD", "ebay_price": "USD",
    "amazon_price": "USD", "coolstuffinc_price": "USD",
}
OBSERVATION_FIELDS = (
    "schema_version", "snapshot_timestamp", "card_id", "card_name", "vendor", "currency",
    "raw_price", "decimal_price", "is_zero_price", "source_collection_cache_key", "source_payload_checksum",
)
AtomicWriter = Callable[[Path, bytes], None]


class PriceSnapshotError(RuntimeError):
    """価格snapshot入力または保存エラー。"""


def _snapshot_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise PriceSnapshotError("collection metadataのfetched_atが不正です")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PriceSnapshotError("collection metadataのfetched_atが不正です") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PriceSnapshotError("collection metadataのfetched_atはUTCである必要があります")
    return parsed.isoformat().replace("+00:00", "Z")


def _card_id(card: dict[str, Any]) -> int:
    value = card.get("id")
    if type(value) is not int or value <= 0:
        raise PriceSnapshotError("card idが不正です")
    return value


def _card_name(card: dict[str, Any]) -> str:
    value = card.get("name")
    if not isinstance(value, str) or not value:
        raise PriceSnapshotError("card nameが不正です")
    return value


def _price(value: Any) -> Decimal:
    if not isinstance(value, str) or not value:
        raise PriceSnapshotError("priceは非空のdecimal stringである必要があります")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PriceSnapshotError("priceがdecimalとして不正です") from exc
    if not parsed.is_finite() or parsed < 0:
        raise PriceSnapshotError("priceはfiniteかつ非負である必要があります")
    return parsed


def build_observations(source: Source) -> tuple[str, list[dict[str, Any]], Counter[str], Counter[str]]:
    """card_pricesをcard-level vendor minimumへ展開する。"""
    timestamp = _snapshot_timestamp(source.metadata.get("fetched_at"))
    seen: set[int] = set(); missing: Counter[str] = Counter(); zero: Counter[str] = Counter(); observations: list[dict[str, Any]] = []
    for card in source.payload["data"]:
        if not isinstance(card, dict):
            raise PriceSnapshotError("card recordはobjectである必要があります")
        card_id = _card_id(card)
        if card_id in seen:
            raise PriceSnapshotError("duplicate card_idは価格snapshotでサポートされません")
        seen.add(card_id); name = _card_name(card)
        values = card.get("card_prices")
        if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
            raise PriceSnapshotError("card_pricesはobjectのlistである必要があります")
        minimums: dict[str, tuple[Decimal, str]] = {}
        for value in values:
            unknown = set(value) - set(VENDORS)
            if unknown:
                raise PriceSnapshotError(f"unknown vendor field: {sorted(unknown)[0]}")
            for vendor in VENDORS:
                if vendor not in value or value[vendor] is None:
                    continue
                raw = value[vendor]; parsed = _price(raw)
                if vendor not in minimums or parsed < minimums[vendor][0]:
                    minimums[vendor] = (parsed, raw)
        for vendor in VENDORS:
            if vendor not in minimums:
                missing[vendor] += 1
        for vendor, (parsed, raw) in minimums.items():
            if parsed == 0:
                zero[vendor] += 1
            observations.append({
                "schema_version": PRICE_OBSERVATION_SCHEMA_VERSION, "snapshot_timestamp": timestamp,
                "card_id": card_id, "card_name": name, "vendor": vendor, "currency": VENDORS[vendor],
                "raw_price": raw, "decimal_price": format(parsed, "f"), "is_zero_price": parsed == 0,
                "source_collection_cache_key": source.metadata["cache_key"], "source_payload_checksum": source.metadata["data_sha256"],
            })
    return timestamp, sorted(observations, key=lambda item: (item["snapshot_timestamp"], item["card_id"], item["vendor"])), missing, zero


def _serialize_jsonl(observations: list[dict[str, Any]]) -> bytes:
    lines = [json.dumps({field: observation[field] for field in OBSERVATION_FIELDS}, ensure_ascii=False, separators=(",", ":")) for observation in observations]
    return b"" if not lines else ("\n".join(lines) + "\n").encode("utf-8")


def _key(source: Source, timestamp: str) -> str:
    payload = {"metadata_schema_version": PRICE_SNAPSHOT_METADATA_SCHEMA_VERSION,
               "observation_schema_version": PRICE_OBSERVATION_SCHEMA_VERSION,
               "snapshot_identifier": PRICE_SNAPSHOT_IDENTIFIER, "ordering_identifier": ORDERING_IDENTIFIER,
               "source_collection_cache_key": source.metadata["cache_key"], "source_payload_checksum": source.metadata["data_sha256"],
               "snapshot_timestamp": timestamp, "vendor_currency_mapping": VENDORS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _metadata_path(output: Path, key: str) -> Path:
    return output / f"price-snapshot-{key[:KEY_PREFIX_LENGTH]}.metadata.json"


def _data_path(output: Path, key: str, checksum: str) -> Path:
    return output / f"price-snapshot-{key[:KEY_PREFIX_LENGTH]}-{checksum[:CONTENT_PREFIX_LENGTH]}.jsonl"


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle: return json.load(handle)


def _expected(source: Source, key: str, timestamp: str, observations: list[dict[str, Any]], missing: Counter[str], zero: Counter[str]) -> dict[str, Any]:
    return {"metadata_schema_version": PRICE_SNAPSHOT_METADATA_SCHEMA_VERSION, "completed": True,
            "price_snapshot_cache_key": key, "snapshot_identifier": PRICE_SNAPSHOT_IDENTIFIER,
            "observation_schema_version": PRICE_OBSERVATION_SCHEMA_VERSION, "output_format": "jsonl",
            "ordering_identifier": ORDERING_IDENTIFIER, "snapshot_timestamp": timestamp,
            "source_collection_metadata_file": source.metadata_path.name, "source_collection_data_file": source.data_path.name,
            "source_collection_cache_key": source.metadata["cache_key"], "source_payload_checksum": source.metadata["data_sha256"],
            "source_record_count": len(source.payload["data"]), "vendor_currency_mapping": VENDORS,
            "observation_count": len(observations), "missing_vendor_field_counts": {vendor: missing[vendor] for vendor in sorted(VENDORS)},
            "missing_vendor_field_count_unit": "card_vendor_pair_with_no_non_null_value_across_all_card_prices_objects_v1",
            "zero_value_counts": {vendor: zero[vendor] for vendor in sorted(VENDORS)},
            "price_policy": "card_level_vendor_minimum_across_versions_no_currency_conversion_no_vendor_merge_missing_count_card_vendor_pair_v1"}


def valid_output(output: Path, key: str, source: Source, timestamp: str, observations: list[dict[str, Any]], missing: Counter[str], zero: Counter[str]) -> bool:
    try:
        metadata = _read_json(_metadata_path(output, key))
        if not isinstance(metadata, dict) or any(metadata.get(field) != value for field, value in _expected(source, key, timestamp, observations, missing, zero).items()): return False
        checksum, size, filename = metadata.get("output_checksum"), metadata.get("output_file_size"), metadata.get("output_data_file")
        if not isinstance(checksum, str) or len(checksum) != 64 or type(size) is not int or size < 0 or filename != _data_path(output, key, checksum).name: return False
        path = _safe_child(output, filename)
        if path is None or not path.is_file(): return False
        raw = path.read_bytes()
        return len(raw) == size and hashlib.sha256(raw).hexdigest() == checksum and b"\r" not in raw and (not raw or raw.endswith(b"\n"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError): return False


def snapshot_prices(input_metadata: Path, output: Path, *, force: bool = False, writer: AtomicWriter = _write_bytes_atomic) -> dict[str, Any]:
    try:
        source = load_source(input_metadata)
    except RuntimeError as exc:
        raise PriceSnapshotError(str(exc)) from exc
    timestamp, observations, missing, zero = build_observations(source); key = _key(source, timestamp); metadata_path = _metadata_path(output, key)
    hit = valid_output(output, key, source, timestamp, observations, missing, zero)
    if hit and not force:
        metadata = _read_json(metadata_path)
        return {"status": "cache_hit", "cache_hit": True, "price_snapshot_cache_key": key, "source": source, "observations": observations, "output_metadata_path": metadata_path, "output_data_path": output / metadata["output_data_file"]}
    content = _serialize_jsonl(observations); data_path = _data_path(output, key, hashlib.sha256(content).hexdigest()); created = False
    try:
        output.mkdir(parents=True, exist_ok=True)
        if data_path.exists():
            if not data_path.is_file() or data_path.read_bytes() != content: raise OSError("同名generationが期待する内容と一致しません")
        else:
            writer(data_path, content); created = True
        metadata = _expected(source, key, timestamp, observations, missing, zero)
        metadata.update({"output_data_file": data_path.name, "output_checksum": hashlib.sha256(content).hexdigest(), "output_file_size": len(content)})
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode())
    except OSError as exc:
        if created:
            try: data_path.unlink(missing_ok=True)
            except OSError: pass
        raise PriceSnapshotError("価格snapshot出力の保存に失敗しました。既存出力は変更していません") from exc
    return {"status": "snapshotted", "cache_hit": hit, "price_snapshot_cache_key": key, "source": source, "observations": observations, "output_metadata_path": metadata_path, "output_data_path": data_path}
