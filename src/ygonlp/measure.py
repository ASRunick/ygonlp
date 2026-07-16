"""前処理済みカードテキストの決定論的なLength Metricsを生成する。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .preprocess import (
    PREPROCESSING_METADATA_SCHEMA_VERSION,
    RECORD_FIELDS as PREPROCESSING_RECORD_FIELDS,
    RECORD_SCHEMA_VERSION as PREPROCESSING_RECORD_SCHEMA_VERSION,
    SORT_ORDER as PREPROCESSING_SORT_ORDER,
)

MEASUREMENT_RECORD_SCHEMA_VERSION = 1
MEASUREMENT_METADATA_SCHEMA_VERSION = 1
CHARACTER_METRIC_VERSION = 1
WORD_METRIC_VERSION = 1
SENTENCE_METRIC_VERSION = 1
OUTPUT_FORMAT = "jsonl"
SORT_ORDER = "card_id_ascending"
TARGET_SELECTION_RULE = "is_effect_text_target_true_and_nonblank_text_normalized"
CHARACTER_METRIC_IDENTIFIER = "python_len_unicode_code_points_v1"
WORD_METRIC_IDENTIFIER = "ascii_alnum_internal_apostrophe_hyphen_comma_v1"
SENTENCE_METRIC_IDENTIFIER = "split_terminal_punctuation_v1"
PROJECT_VERSION = "0.0.0"
KEY_PREFIX_LENGTH = 16
CONTENT_PREFIX_LENGTH = 16

WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['’,-][A-Za-z0-9]+)*")
SENTENCE_DELIMITER = re.compile(r"[.!?]+")

RECORD_FIELDS = (
    "schema_version",
    "card_id",
    "name",
    "card_type",
    "frame_type",
    "tcg_date",
    "text_normalized",
    "character_count",
    "word_count",
    "sentence_count",
)


class MeasureError(RuntimeError):
    """測定入力または保存のFatal error。"""


AtomicWriter = Callable[[Path, bytes], None]


@dataclass(frozen=True)
class Source:
    metadata_path: Path
    data_path: Path
    metadata: dict[str, Any]
    records: list[dict[str, Any]]


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


def _is_int(value: Any) -> bool:
    return type(value) is int


def _validate_preprocessed_record(record: Any, previous_card_id: int | None) -> int:
    if not isinstance(record, dict) or tuple(record) != PREPROCESSING_RECORD_FIELDS:
        raise MeasureError("前処理JSONLのrecord schemaが不正です")
    if not _is_int(record.get("schema_version")) or record["schema_version"] != PREPROCESSING_RECORD_SCHEMA_VERSION:
        raise MeasureError("前処理JSONLのrecord schema versionが不正です")
    if not _is_int(record.get("card_id")):
        raise MeasureError("前処理JSONLのcard_idが不正です")
    card_id = record["card_id"]
    if previous_card_id is not None and card_id <= previous_card_id:
        raise MeasureError("前処理JSONLのcard_id順序または一意性が不正です")
    for field in ("name", "card_type", "frame_type", "text_kind"):
        if not isinstance(record.get(field), str):
            raise MeasureError(f"前処理JSONLの{field}が不正です")
    for field in ("race", "archetype", "text_raw", "text_normalized", "exclusion_reason", "tcg_date", "ocg_date"):
        if record.get(field) is not None and not isinstance(record.get(field), str):
            raise MeasureError(f"前処理JSONLの{field}が不正です")
    if type(record.get("has_text")) is not bool or type(record.get("is_effect_text_target")) is not bool:
        raise MeasureError("前処理JSONLのboolean fieldが不正です")
    if not _is_int(record.get("source_index")):
        raise MeasureError("前処理JSONLのsource_indexが不正です")
    return card_id


def load_source(metadata_path: Path) -> Source:
    """前処理metadataを信頼境界として測定入力を完全に検証する。"""
    try:
        metadata = _read_json(metadata_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeasureError("前処理metadataを読み込めません") from exc
    if not isinstance(metadata, dict):
        raise MeasureError("前処理metadataはobjectである必要があります")
    if metadata.get("metadata_schema_version") != PREPROCESSING_METADATA_SCHEMA_VERSION:
        raise MeasureError("前処理metadata schema versionが対応していません")
    if metadata.get("completed") is not True:
        raise MeasureError("前処理metadataが完了状態ではありません")
    if metadata.get("record_schema_version") != PREPROCESSING_RECORD_SCHEMA_VERSION:
        raise MeasureError("前処理record schema versionが対応していません")
    if metadata.get("sort_order") != PREPROCESSING_SORT_ORDER:
        raise MeasureError("前処理JSONLのsort orderが対応していません")
    if not isinstance(metadata.get("preprocessing_cache_key"), str) or not metadata["preprocessing_cache_key"]:
        raise MeasureError("前処理metadataのcache keyが不正です")
    if not _is_int(metadata.get("output_record_count")) or metadata["output_record_count"] < 0:
        raise MeasureError("前処理metadataのoutput record countが不正です")
    if metadata["output_record_count"] == 0:
        raise MeasureError("0件の前処理JSONLは前処理metadata契約上サポートされません")
    if not isinstance(metadata.get("output_sha256"), str) or not metadata["output_sha256"]:
        raise MeasureError("前処理metadataのoutput checksumが不正です")
    data_path = _safe_child(metadata_path.parent, metadata.get("output_data_file"))
    if data_path is None or not data_path.is_file():
        raise MeasureError("前処理metadataが指すJSONL fileが不正です")
    try:
        raw = data_path.read_bytes()
    except OSError as exc:
        raise MeasureError("前処理JSONLを読み込めません") from exc
    if hashlib.sha256(raw).hexdigest() != metadata["output_sha256"]:
        raise MeasureError("前処理JSONL checksumがmetadataと一致しません")
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise MeasureError("前処理JSONLのエンコーディングまたは改行が不正です")
    try:
        lines = raw.decode("utf-8").splitlines()
        records = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeasureError("前処理JSONLをparseできません") from exc
    if len(records) != metadata["output_record_count"]:
        raise MeasureError("前処理JSONLのrecord countがmetadataと一致しません")
    previous_card_id: int | None = None
    for record in records:
        previous_card_id = _validate_preprocessed_record(record, previous_card_id)
    return Source(metadata_path=metadata_path, data_path=data_path, metadata=metadata, records=records)


def character_count(text: str) -> int:
    """Unicode code point数を返す。結合文字も個別のcode pointとして数える。"""
    return len(text)


def word_count(text: str) -> int:
    """固定のASCII中心regexに一致する語の数を返す。"""
    return len(WORD_PATTERN.findall(text))


def sentence_count(text: str) -> int:
    """終端記号で分割した非空断片数を返す。delimiterだけなら0。"""
    return sum(bool(part.strip()) for part in SENTENCE_DELIMITER.split(text))


def _is_measurement_target(record: dict[str, Any]) -> bool:
    text = record["text_normalized"]
    return record["is_effect_text_target"] is True and isinstance(text, str) and bool(text.strip())


def measure_records(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    measured: list[dict[str, Any]] = []
    empty_target_text_count = 0
    for record in records:
        text = record["text_normalized"]
        if record["is_effect_text_target"] is True and isinstance(text, str) and not text.strip():
            empty_target_text_count += 1
        if not _is_measurement_target(record):
            continue
        assert isinstance(text, str)
        measured.append({
            "schema_version": MEASUREMENT_RECORD_SCHEMA_VERSION,
            "card_id": record["card_id"],
            "name": record["name"],
            "card_type": record["card_type"],
            "frame_type": record["frame_type"],
            "tcg_date": record["tcg_date"],
            "text_normalized": text,
            "character_count": character_count(text),
            "word_count": word_count(text),
            "sentence_count": sentence_count(text),
        })
    return measured, empty_target_text_count


def serialize_jsonl(records: Iterable[dict[str, Any]]) -> bytes:
    lines = [json.dumps({field: record[field] for field in RECORD_FIELDS}, ensure_ascii=False, separators=(",", ":")) for record in records]
    return b"" if not lines else ("\n".join(lines) + "\n").encode("utf-8")


def measurement_cache_key(source: Source) -> str:
    payload = {
        "measurement_metadata_schema_version": MEASUREMENT_METADATA_SCHEMA_VERSION,
        "measurement_record_schema_version": MEASUREMENT_RECORD_SCHEMA_VERSION,
        "character_metric_version": CHARACTER_METRIC_VERSION,
        "word_metric_version": WORD_METRIC_VERSION,
        "sentence_metric_version": SENTENCE_METRIC_VERSION,
        "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
        "source_preprocessing_checksum": source.metadata["output_sha256"],
        "source_record_count": source.metadata["output_record_count"],
        "target_selection_rule": TARGET_SELECTION_RULE,
        "output_format": OUTPUT_FORMAT,
        "sort_order": SORT_ORDER,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def output_metadata_path(output: Path, key: str) -> Path:
    return output / f"cards-measured-{key[:KEY_PREFIX_LENGTH]}.metadata.json"


def output_data_path(output: Path, key: str, checksum: str) -> Path:
    return output / f"cards-measured-{key[:KEY_PREFIX_LENGTH]}-{checksum[:CONTENT_PREFIX_LENGTH]}.jsonl"


def _valid_measured_record(record: Any, previous_card_id: int | None) -> int | None:
    if not isinstance(record, dict) or tuple(record) != RECORD_FIELDS:
        return None
    if record.get("schema_version") != MEASUREMENT_RECORD_SCHEMA_VERSION or not _is_int(record.get("card_id")):
        return None
    card_id = record["card_id"]
    if previous_card_id is not None and card_id <= previous_card_id:
        return None
    if any(not isinstance(record.get(field), str) for field in ("name", "card_type", "frame_type", "text_normalized")):
        return None
    if record.get("tcg_date") is not None and not isinstance(record.get("tcg_date"), str):
        return None
    if any(not _is_int(record.get(field)) or record[field] < 0 for field in ("character_count", "word_count", "sentence_count")):
        return None
    return card_id


def valid_output(output: Path, key: str) -> bool:
    try:
        metadata = _read_json(output_metadata_path(output, key))
        if not isinstance(metadata, dict) or metadata.get("completed") is not True:
            return False
        required_versions = {
            "metadata_schema_version": MEASUREMENT_METADATA_SCHEMA_VERSION,
            "measurement_record_schema_version": MEASUREMENT_RECORD_SCHEMA_VERSION,
            "character_metric_version": CHARACTER_METRIC_VERSION,
            "word_metric_version": WORD_METRIC_VERSION,
            "sentence_metric_version": SENTENCE_METRIC_VERSION,
            "measurement_cache_key": key,
            "output_format": OUTPUT_FORMAT,
            "sort_order": SORT_ORDER,
        }
        if any(metadata.get(field) != value for field, value in required_versions.items()):
            return False
        if not _is_int(metadata.get("output_file_size")) or metadata["output_file_size"] < 0:
            return False
        if not _is_int(metadata.get("measured_record_count")) or metadata["measured_record_count"] < 0:
            return False
        data_path = _safe_child(output, metadata.get("output_data_file"))
        if data_path is None or not data_path.is_file():
            return False
        raw = data_path.read_bytes()
        if len(raw) != metadata["output_file_size"] or hashlib.sha256(raw).hexdigest() != metadata.get("output_checksum"):
            return False
        if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
            return False
        if not raw:
            return metadata["measured_record_count"] == 0
        if not raw.endswith(b"\n"):
            return False
        records = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        if len(records) != metadata["measured_record_count"]:
            return False
        previous_card_id: int | None = None
        for record in records:
            previous_card_id = _valid_measured_record(record, previous_card_id)
            if previous_card_id is None:
                return False
        return True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return False


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
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def measure(
    input_metadata: Path,
    output: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    writer: AtomicWriter = _write_atomic,
) -> dict[str, Any]:
    source = load_source(input_metadata)
    key = measurement_cache_key(source)
    metadata_path = output_metadata_path(output, key)
    hit = valid_output(output, key)
    if hit and not force and not dry_run:
        metadata = _read_json(metadata_path)
        return {
            "measurement_cache_key": key, "source": source, "output_metadata_path": metadata_path,
            "output_data_path": output / metadata["output_data_file"], "cache_hit": True,
            "status": "cache_hit", "measured_records": [], "empty_target_text_count": 0,
        }
    measured_records, empty_target_text_count = measure_records(source.records)
    plan = {
        "measurement_cache_key": key, "source": source, "output_metadata_path": metadata_path,
        "cache_hit": hit, "measured_records": measured_records, "empty_target_text_count": empty_target_text_count,
    }
    if dry_run:
        return plan
    content = serialize_jsonl(measured_records)
    checksum = hashlib.sha256(content).hexdigest()
    data_path = output_data_path(output, key, checksum)
    created_data = False
    try:
        output.mkdir(parents=True, exist_ok=True)
        if data_path.exists():
            if not data_path.is_file() or data_path.read_bytes() != content:
                raise OSError("同名のmeasurement generationが期待する内容と一致しません")
        else:
            writer(data_path, content)
            created_data = True
        metadata = {
            "metadata_schema_version": MEASUREMENT_METADATA_SCHEMA_VERSION,
            "completed": True,
            "measurement_cache_key": key,
            "measurement_record_schema_version": MEASUREMENT_RECORD_SCHEMA_VERSION,
            "character_metric_version": CHARACTER_METRIC_VERSION,
            "word_metric_version": WORD_METRIC_VERSION,
            "sentence_metric_version": SENTENCE_METRIC_VERSION,
            "created_at": _utc_now(),
            "source_preprocessing_metadata_file": source.metadata_path.name,
            "source_preprocessing_data_file": source.data_path.name,
            "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
            "source_preprocessing_checksum": source.metadata["output_sha256"],
            "input_record_count": len(source.records),
            "measured_record_count": len(measured_records),
            "excluded_record_count": len(source.records) - len(measured_records),
            "empty_target_text_count": empty_target_text_count,
            "output_data_file": data_path.name,
            "output_checksum": checksum,
            "output_file_size": len(content),
            "sort_order": SORT_ORDER,
            "output_format": OUTPUT_FORMAT,
            "character_metric_identifier": CHARACTER_METRIC_IDENTIFIER,
            "word_metric_identifier": WORD_METRIC_IDENTIFIER,
            "sentence_metric_identifier": SENTENCE_METRIC_IDENTIFIER,
            "target_selection_rule": TARGET_SELECTION_RULE,
            "project_version": PROJECT_VERSION,
        }
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        if created_data:
            _best_effort_unlink(data_path)
        raise MeasureError("測定出力の保存に失敗しました。既存出力は変更していません") from exc
    return {**plan, "status": "measured", "output_data_path": data_path}


def dry_run_lines(input_metadata: Path, output: Path, *, force: bool = False) -> list[str]:
    plan = measure(input_metadata, output, force=force, dry_run=True)
    source: Source = plan["source"]
    measured_count = len(plan["measured_records"])
    return [
        f"input metadata path: {source.metadata_path}",
        f"resolved preprocessing JSONL path: {source.data_path}",
        f"source preprocessing cache key: {source.metadata['preprocessing_cache_key']}",
        f"source checksum: {source.metadata['output_sha256']}",
        f"input record count: {len(source.records)}",
        f"measurement target count: {measured_count}",
        f"excluded count: {len(source.records) - measured_count}",
        f"empty target text count: {plan['empty_target_text_count']}",
        f"output directory: {output}",
        f"JSONL naming policy: cards-measured-{plan['measurement_cache_key'][:KEY_PREFIX_LENGTH]}-<content-sha256-prefix>.jsonl",
        f"measurement metadata path: {plan['output_metadata_path']}",
        f"measurement cache key: {plan['measurement_cache_key']}",
        f"metric versions: character={CHARACTER_METRIC_VERSION}, word={WORD_METRIC_VERSION}, sentence={SENTENCE_METRIC_VERSION}",
        f"valid existing output: {'yes' if plan['cache_hit'] else 'no'}",
        f"--force: {'yes' if force else 'no'}",
        f"measurement required: {'yes' if force or not plan['cache_hit'] else 'no'}",
    ]
