"""Issue #1のraw cacheを決定論的JSONLへ正規化する。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

PREPROCESSING_SCHEMA_VERSION = 1
RECORD_SCHEMA_VERSION = 1
NORMALIZATION_VERSION = 1
TARGET_POLICY_VERSION = 1
PREPROCESSING_METADATA_SCHEMA_VERSION = 1
OUTPUT_FORMAT = "jsonl"
SORT_ORDER = "card_id_ascending"
KEY_PREFIX_LENGTH = 16
CONTENT_PREFIX_LENGTH = 16

RECORD_FIELDS = (
    "schema_version", "card_id", "name", "card_type", "frame_type", "race", "archetype",
    "text_raw", "text_normalized", "text_kind", "has_text", "is_effect_text_target",
    "exclusion_reason", "tcg_date", "ocg_date", "source_index",
)

TARGET_CARD_TYPES = frozenset({
    "Effect Monster", "Flip Effect Monster", "Gemini Monster", "Union Effect Monster",
    "Spirit Monster", "Toon Monster", "Tuner Monster", "Synchro Tuner Monster",
    "Fusion Monster", "Synchro Monster", "XYZ Monster", "Link Monster", "Ritual Effect Monster",
    "Pendulum Effect Monster", "Pendulum Tuner Effect Monster", "Pendulum Effect Fusion Monster",
    "Pendulum Flip Effect Monster", "XYZ Pendulum Effect Monster", "Synchro Pendulum Effect Monster",
    "Pendulum Effect Ritual Monster", "Flip Tuner Effect Monster", "Spell Card", "Trap Card",
})
KNOWN_NON_TARGET_TYPES = frozenset({
    "Normal Monster", "Pendulum Normal Monster", "Normal Tuner Monster", "Ritual Monster", "Token", "Skill Card",
})


class PreprocessError(RuntimeError):
    """入力または保存のFatal error。"""


AtomicWriter = Callable[[Path, bytes], None]


@dataclass(frozen=True)
class Source:
    metadata_path: Path
    data_path: Path
    metadata: dict[str, Any]
    payload: dict[str, Any]


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _safe_child(directory: Path, name: Any) -> Path | None:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        return None
    relative = Path(name)
    if relative.name != name or ".." in relative.parts:
        return None
    candidate = directory / relative
    try:
        if candidate.resolve(strict=False).parent != directory.resolve(strict=False):
            return None
    except OSError:
        return None
    return candidate


def load_source(metadata_path: Path) -> Source:
    """Issue #1 metadataを信頼境界としてraw dataを検証・解決する。"""
    try:
        metadata = _read_json(metadata_path)
    except (OSError, ValueError) as exc:
        raise PreprocessError("raw metadataを読み込めません") from exc
    if not isinstance(metadata, dict):
        raise PreprocessError("raw metadataはobjectである必要があります")
    if metadata.get("schema_version") != "1" or metadata.get("completed") is not True:
        raise PreprocessError("raw metadataのschema versionまたは完了状態が不正です")
    if not isinstance(metadata.get("cache_key"), str):
        raise PreprocessError("raw metadataのcache keyが不正です")
    data_path = _safe_child(metadata_path.parent, metadata.get("data_file"))
    if data_path is None or not data_path.is_file():
        raise PreprocessError("raw metadataが指すdata fileが不正です")
    try:
        raw = data_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != metadata.get("data_sha256"):
            raise PreprocessError("raw data checksumがmetadataと一致しません")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, PreprocessError):
            raise
        raise PreprocessError("raw dataを読み込めません") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise PreprocessError("raw dataは非空のdata listを持つobjectである必要があります")
    if len(payload["data"]) != metadata.get("record_count"):
        raise PreprocessError("raw record countがmetadataと一致しません")
    return Source(metadata_path=metadata_path, data_path=data_path, metadata=metadata, payload=payload)


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _optional_string(card: dict[str, Any], field: str, warnings: Counter[str]) -> str | None:
    value = card.get(field)
    if value is None:
        warnings[f"missing_{field}"] += 1
        return None
    if not isinstance(value, str):
        warnings["invalid_optional_field"] += 1
        return None
    return value


def _required(card: dict[str, Any], field: str, value_type: type) -> Any:
    value = card.get(field)
    if not isinstance(value, value_type) or (value_type is int and isinstance(value, bool)):
        raise PreprocessError(f"必須field {field} が不正です")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _merge_misc_info(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        raise PreprocessError("misc_infoはlistである必要があります")
    if not value:
        return {}
    if not all(isinstance(item, dict) for item in value):
        raise PreprocessError("misc_info要素はobjectである必要があります")
    unique = {_canonical(item) for item in value}
    if len(unique) == 1:
        return value[0]
    non_date = {_canonical({k: v for k, v in item.items() if k not in {"tcg_date", "ocg_date"}}) for item in value}
    if len(non_date) != 1:
        raise PreprocessError("複数misc_info要素が曖昧です")
    merged = dict(value[0])
    for field in ("tcg_date", "ocg_date"):
        nonempty = {item.get(field) for item in value if item.get(field) not in (None, "")}
        if len(nonempty) > 1:
            raise PreprocessError(f"複数misc_info要素の{field}が競合しています")
        merged[field] = next(iter(nonempty), None)
    return merged


def normalize_date(value: Any, field: str, warnings: Counter[str]) -> str | None:
    if value is None or value == "":
        warnings[f"missing_{field}"] += 1
        return None
    if not isinstance(value, str):
        raise PreprocessError(f"{field}の型が不正です")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        warnings["invalid_optional_date"] += 1
        return None
    if parsed.isoformat() != value:
        warnings["invalid_optional_date"] += 1
        return None
    return value


def classify_target(card_type: str, frame_type: str, has_effect: Any, text_raw: str | None, warnings: Counter[str]) -> tuple[str, bool, str | None]:
    if text_raw is None:
        return "missing_text", False, "missing_text"
    if text_raw == "":
        return "missing_text", False, "empty_text"
    if card_type == "Token":
        return "token_text", False, "token"
    if card_type == "Skill Card":
        return "skill_text", False, "skill_card"
    if frame_type in {"normal", "normal_pendulum"}:
        return "flavor_text", False, "normal_monster_flavor_text"
    if card_type not in TARGET_CARD_TYPES and card_type not in KNOWN_NON_TARGET_TYPES:
        warnings["unknown_card_type"] += 1
        return "unknown_text", False, "unknown_card_type"
    if card_type in {"Spell Card", "Trap Card"}:
        return "effect_or_rule_text", True, None
    if card_type in TARGET_CARD_TYPES and has_effect == 1:
        return "effect_or_rule_text", True, None
    return "flavor_text", False, "not_in_target_policy"


def transform_cards(cards: Iterable[Any]) -> tuple[list[dict[str, Any]], Counter[str], int]:
    warnings: Counter[str] = Counter()
    by_id: dict[int, tuple[dict[str, Any], int, str]] = {}
    duplicates = 0
    for source_index, raw_card in enumerate(cards):
        if not isinstance(raw_card, dict):
            raise PreprocessError("data list要素はobjectである必要があります")
        card_id = _required(raw_card, "id", int)
        fingerprint = _canonical(raw_card)
        if card_id in by_id:
            previous, previous_index, previous_fingerprint = by_id[card_id]
            if fingerprint != previous_fingerprint:
                raise PreprocessError("同一card_idの内容が一致しません")
            duplicates += 1
            warnings["duplicate_card_id"] += 1
            continue
        name = _required(raw_card, "name", str)
        card_type = _required(raw_card, "type", str)
        frame_type = _required(raw_card, "frameType", str)
        race = _optional_string(raw_card, "race", warnings)
        archetype = _optional_string(raw_card, "archetype", warnings)
        text_raw = _optional_string(raw_card, "desc", warnings)
        if text_raw is None:
            warnings["missing_text"] += 1
        elif text_raw == "":
            warnings["empty_text"] += 1
        misc = _merge_misc_info(raw_card.get("misc_info"))
        tcg_date = normalize_date(misc.get("tcg_date"), "tcg_date", warnings)
        ocg_date = normalize_date(misc.get("ocg_date"), "ocg_date", warnings)
        text_kind, target, exclusion = classify_target(card_type, frame_type, misc.get("has_effect"), text_raw, warnings)
        record = {
            "schema_version": RECORD_SCHEMA_VERSION, "card_id": card_id, "name": name,
            "card_type": card_type, "frame_type": frame_type, "race": race, "archetype": archetype,
            "text_raw": text_raw, "text_normalized": normalize_text(text_raw), "text_kind": text_kind,
            "has_text": text_raw is not None and text_raw != "", "is_effect_text_target": target,
            "exclusion_reason": exclusion, "tcg_date": tcg_date, "ocg_date": ocg_date,
            "source_index": source_index,
        }
        by_id[card_id] = (record, source_index, fingerprint)
    records = [item[0] for _, item in sorted(by_id.items())]
    return records, warnings, duplicates


def serialize_jsonl(records: Iterable[dict[str, Any]]) -> bytes:
    lines = []
    for record in records:
        ordered = {field: record[field] for field in RECORD_FIELDS}
        lines.append(json.dumps(ordered, ensure_ascii=False, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode("utf-8")


def preprocessing_cache_key(source: Source) -> str:
    payload = {
        "preprocessing_metadata_schema_version": PREPROCESSING_METADATA_SCHEMA_VERSION,
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "target_policy_version": TARGET_POLICY_VERSION,
        "source_cache_key": source.metadata["cache_key"],
        "source_checksum": source.metadata["data_sha256"],
        "source_record_count": source.metadata["record_count"],
        "output_format": OUTPUT_FORMAT,
        "sort_order": SORT_ORDER,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def output_metadata_path(output: Path, key: str) -> Path:
    return output / f"cards-normalized-{key[:KEY_PREFIX_LENGTH]}.metadata.json"


def output_data_path(output: Path, key: str, checksum: str) -> Path:
    return output / f"cards-normalized-{key[:KEY_PREFIX_LENGTH]}-{checksum[:CONTENT_PREFIX_LENGTH]}.jsonl"


def _is_int(value: Any) -> bool:
    return type(value) is int


def validate_preprocessed_record(
    record: Any,
    previous_card_id: int | None,
) -> int:
    """前処理JSONLの1 recordをschema・型・順序まで検証する。"""
    if not isinstance(record, dict) or tuple(record) != RECORD_FIELDS:
        raise PreprocessError("前処理JSONLのrecord schemaまたはキー順が不正です")

    if (
        not _is_int(record.get("schema_version"))
        or record["schema_version"] != RECORD_SCHEMA_VERSION
    ):
        raise PreprocessError("前処理JSONLのrecord schema versionが不正です")

    if not _is_int(record.get("card_id")):
        raise PreprocessError("前処理JSONLのcard_idが不正です")

    card_id = record["card_id"]
    if previous_card_id is not None and card_id <= previous_card_id:
        raise PreprocessError("前処理JSONLのcard_id順序または一意性が不正です")

    for field in ("name", "card_type", "frame_type", "text_kind"):
        if not isinstance(record.get(field), str):
            raise PreprocessError(f"前処理JSONLの{field}が不正です")

    for field in (
        "race",
        "archetype",
        "text_raw",
        "text_normalized",
        "exclusion_reason",
        "tcg_date",
        "ocg_date",
    ):
        if record.get(field) is not None and not isinstance(record.get(field), str):
            raise PreprocessError(f"前処理JSONLの{field}が不正です")

    if (
        type(record.get("has_text")) is not bool
        or type(record.get("is_effect_text_target")) is not bool
    ):
        raise PreprocessError("前処理JSONLのboolean fieldが不正です")

    if not _is_int(record.get("source_index")):
        raise PreprocessError("前処理JSONLのsource_indexが不正です")

    return card_id


def valid_output(output: Path, key: str) -> bool:
    try:
        metadata = _read_json(output_metadata_path(output, key))
        if not isinstance(metadata, dict) or metadata.get("completed") is not True:
            return False
        if metadata.get("metadata_schema_version") != PREPROCESSING_METADATA_SCHEMA_VERSION or metadata.get("preprocessing_cache_key") != key:
            return False
        data_path = _safe_child(output, metadata.get("output_data_file"))
        if data_path is None or not data_path.is_file():
            return False
        raw = data_path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n") or b"\r" in raw:
            return False
        if hashlib.sha256(raw).hexdigest() != metadata.get("output_sha256"):
            return False
        lines = raw.decode("utf-8").splitlines()
        if len(lines) != metadata.get("output_record_count") or not lines:
            return False
        records = [json.loads(line) for line in lines]
        return all(list(record) == list(RECORD_FIELDS) for record in records)
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return False

def verify_preprocessed_cache(metadata_path: Path) -> dict[str, Any]:
    """前処理cacheを全record単位で深く検証する。"""
    try:
        metadata = _read_json(metadata_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreprocessError("前処理metadataを読み込めません") from exc

    if not isinstance(metadata, dict):
        raise PreprocessError("前処理metadataはobjectである必要があります")

    required_metadata = {
        "metadata_schema_version": PREPROCESSING_METADATA_SCHEMA_VERSION,
        "completed": True,
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "sort_order": SORT_ORDER,
        "output_format": OUTPUT_FORMAT,
    }
    if any(metadata.get(field) != value for field, value in required_metadata.items()):
        raise PreprocessError("前処理metadataのschema、完了状態、または出力定義が不正です")

    if (
        not isinstance(metadata.get("preprocessing_cache_key"), str)
        or not metadata["preprocessing_cache_key"]
    ):
        raise PreprocessError("前処理metadataのcache keyが不正です")

    if (
        not _is_int(metadata.get("output_record_count"))
        or metadata["output_record_count"] <= 0
    ):
        raise PreprocessError("前処理metadataのoutput record countが不正です")

    checksum = metadata.get("output_sha256")
    if not isinstance(checksum, str) or len(checksum) != 64:
        raise PreprocessError("前処理metadataのoutput checksumが不正です")

    data_path = _safe_child(metadata_path.parent, metadata.get("output_data_file"))
    if data_path is None or not data_path.is_file():
        raise PreprocessError("前処理metadataが指すJSONL fileが不正です")

    try:
        raw = data_path.read_bytes()
    except OSError as exc:
        raise PreprocessError("前処理JSONLを読み込めません") from exc

    if hashlib.sha256(raw).hexdigest() != checksum:
        raise PreprocessError("前処理JSONL checksumがmetadataと一致しません")

    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise PreprocessError("前処理JSONLのエンコーディングまたは改行が不正です")

    try:
        lines = raw.decode("utf-8").splitlines()
        records = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreprocessError("前処理JSONLをparseできません") from exc

    if len(records) != metadata["output_record_count"]:
        raise PreprocessError("前処理JSONLのrecord countがmetadataと一致しません")

    previous_card_id: int | None = None
    for record in records:
        previous_card_id = validate_preprocessed_record(record, previous_card_id)

    return {
        "status": "valid",
        "metadata_path": metadata_path,
        "data_path": data_path,
        "record_count": len(records),
        "preprocessing_cache_key": metadata["preprocessing_cache_key"],
        "output_sha256": checksum,
    }


def _write_atomic(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        # cleanupは本来の書込み・replace失敗を覆い隠してはならない。
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _best_effort_unlink(path: Path) -> None:
    """失敗した新generationの掃除は、元の保存失敗を隠さない。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def preprocess(
    input_metadata: Path,
    output: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    writer: AtomicWriter = _write_atomic,
) -> dict[str, Any]:
    source = load_source(input_metadata)
    key = preprocessing_cache_key(source)
    output_metadata = output_metadata_path(output, key)
    hit = valid_output(output, key)
    if hit and not force:
        metadata = _read_json(output_metadata)
        if not dry_run:
            return {
                "preprocessing_cache_key": key, "source": source, "output_metadata_path": output_metadata,
                "cache_hit": True, "status": "cache_hit", "output_data_path": output / metadata["output_data_file"],
                "warnings": Counter(), "duplicates": 0,
            }
    records, warnings, duplicates = transform_cards(source.payload["data"])
    plan = {
        "preprocessing_cache_key": key, "source": source, "records": records, "warnings": warnings,
        "duplicates": duplicates, "output_metadata_path": output_metadata, "cache_hit": hit,
    }
    if dry_run:
        return plan

    content = serialize_jsonl(records)
    checksum = hashlib.sha256(content).hexdigest()
    data_path = output_data_path(output, key, checksum)
    output.mkdir(parents=True, exist_ok=True)
    created_data = False
    try:
        if data_path.exists():
            if not data_path.is_file() or data_path.read_bytes() != content:
                raise OSError("同名のJSONL generationが期待する内容と一致しません")
        else:
            writer(data_path, content)
            created_data = True
        target_count = sum(record["is_effect_text_target"] for record in records)
        metadata = {
            "metadata_schema_version": PREPROCESSING_METADATA_SCHEMA_VERSION, "completed": True,
            "preprocessing_cache_key": key, "preprocessing_schema_version": PREPROCESSING_SCHEMA_VERSION,
            "record_schema_version": RECORD_SCHEMA_VERSION, "normalization_version": NORMALIZATION_VERSION,
            "target_policy_version": TARGET_POLICY_VERSION, "created_at": _utc_now(),
            "source_raw_metadata_file": source.metadata_path.name, "source_raw_data_file": source.data_path.name,
            "source_cache_key": source.metadata["cache_key"], "source_checksum": source.metadata["data_sha256"],
            "source_record_count": source.metadata["record_count"], "output_data_file": data_path.name,
            "output_sha256": checksum, "output_record_count": len(records), "duplicate_count": duplicates,
            "target_record_count": target_count, "excluded_record_count": len(records) - target_count,
            "missing_text_count": warnings["missing_text"] + warnings["empty_text"],
            "missing_tcg_date_count": warnings["missing_tcg_date"], "missing_ocg_date_count": warnings["missing_ocg_date"],
            "unknown_type_count": warnings["unknown_card_type"], "warning_counts": dict(sorted(warnings.items())),
            "sort_order": SORT_ORDER, "output_format": OUTPUT_FORMAT,
        }
        writer(output_metadata, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        if created_data:
            _best_effort_unlink(data_path)
        raise PreprocessError("前処理出力の保存に失敗しました。既存出力は変更していません") from exc
    return {**plan, "status": "processed", "output_data_path": data_path}


def dry_run_lines(input_metadata: Path, output: Path, *, force: bool = False) -> list[str]:
    plan = preprocess(input_metadata, output, force=force, dry_run=True)
    source: Source = plan["source"]
    return [
        f"input metadata path: {source.metadata_path}", f"resolved raw data path: {source.data_path}",
        f"source cache key: {source.metadata['cache_key']}", f"source checksum: {source.metadata['data_sha256']}",
        f"input record count: {source.metadata['record_count']}", f"output directory: {output}",
        f"JSONL naming policy: cards-normalized-{plan['preprocessing_cache_key'][:KEY_PREFIX_LENGTH]}-<content-sha256-prefix>.jsonl",
        f"preprocessing metadata path: {plan['output_metadata_path']}",
        f"preprocessing cache key: {plan['preprocessing_cache_key']}",
        f"versions: preprocessing={PREPROCESSING_SCHEMA_VERSION}, normalization={NORMALIZATION_VERSION}, target-policy={TARGET_POLICY_VERSION}, metadata={PREPROCESSING_METADATA_SCHEMA_VERSION}",
        f"output sort order: {SORT_ORDER}", f"valid existing output: {'yes' if plan['cache_hit'] else 'no'}",
        f"--force: {'yes' if force else 'no'}", f"conversion required: {'yes' if force or not plan['cache_hit'] else 'no'}",
        f"warning counts: {json.dumps(dict(sorted(plan['warnings'].items())), separators=(',', ':'))}",
    ]
