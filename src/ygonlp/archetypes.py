"""検証済み前処理JSONLからarchetype別テキストprofileを集計・保存する。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .measure import Source, _best_effort_unlink, _safe_child, _write_atomic, character_count, load_source, sentence_count, word_count

ARCHETYPE_METADATA_SCHEMA_VERSION = 1
ARCHETYPE_JSON_SCHEMA_VERSION = 1
ARCHETYPE_IDENTIFIER = "archetype_text_profile_v1"
METRIC_DEFINITION = "text_normalized_unicode_length_word_regex_terminal_punctuation_sentence_v1"
OUTPUT_FORMAT_ORDER = ("json", "csv", "markdown")
OUTPUT_SUFFIXES = {"json": "json", "csv": "csv", "markdown": "md"}
OUTPUT_ORDER = "archetype_ascending_then_card_type_ascending_v1"
CSV_FIELDS = ("archetype", "card_count", "average_character_count", "average_word_count", "average_sentence_count", "card_type", "card_type_count")
AtomicWriter = Callable[[Path, bytes], None]


class ArchetypeError(RuntimeError):
    """archetype profile分析入力または保存のFatal error。"""


def _rounded(value: float) -> float:
    result = round(value, 6)
    return 0.0 if result == 0 else result


def build_archetype_profiles(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """archetypeを持つ全cardを、normalized textの基本指標で決定論的に集計する。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing = 0
    for record in records:
        archetype = record["archetype"]
        if not isinstance(archetype, str) or not archetype:
            missing += 1
            continue
        groups[archetype].append(record)
    profiles = []
    for archetype in sorted(groups):
        cards = groups[archetype]
        texts = [record["text_normalized"] if isinstance(record["text_normalized"], str) else "" for record in cards]
        distribution = Counter(record["card_type"] for record in cards)
        profiles.append({
            "archetype": archetype,
            "card_count": len(cards),
            "average_character_count": _rounded(sum(character_count(text) for text in texts) / len(cards)),
            "average_word_count": _rounded(sum(word_count(text) for text in texts) / len(cards)),
            "average_sentence_count": _rounded(sum(sentence_count(text) for text in texts) / len(cards)),
            "card_type_distribution": [
                {"card_type": card_type, "card_count": distribution[card_type]}
                for card_type in sorted(distribution)
            ],
        })
    return {
        "schema_version": ARCHETYPE_JSON_SCHEMA_VERSION,
        "analysis_identifier": ARCHETYPE_IDENTIFIER,
        "metric_definition": METRIC_DEFINITION,
        "missing_archetype_count": missing,
        "included_record_count": sum(profile["card_count"] for profile in profiles),
        "output_ordering_identifier": OUTPUT_ORDER,
        "archetypes": profiles,
    }


def archetype_cache_key(source: Source) -> str:
    payload = {
        "metadata_schema_version": ARCHETYPE_METADATA_SCHEMA_VERSION,
        "json_schema_version": ARCHETYPE_JSON_SCHEMA_VERSION,
        "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
        "source_preprocessing_checksum": source.metadata["output_sha256"],
        "metric_definition": METRIC_DEFINITION,
        "output_order": OUTPUT_ORDER,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def output_metadata_path(output: Path, key: str) -> Path:
    return output / f"archetypes-{key[:16]}.metadata.json"


def _output_path(output: Path, key: str, checksum: str, format_name: str) -> Path:
    return output / f"archetypes-{key[:16]}-{checksum[:16]}.{OUTPUT_SUFFIXES[format_name]}"


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"archetype": profile["archetype"], "card_count": profile["card_count"],
             "average_character_count": profile["average_character_count"], "average_word_count": profile["average_word_count"],
             "average_sentence_count": profile["average_sentence_count"], "card_type": item["card_type"], "card_type_count": item["card_count"]}
            for profile in result["archetypes"] for item in profile["card_type_distribution"]]


def serialize_json(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def serialize_csv(result: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in _rows(result): writer.writerow(row)
    return output.getvalue().encode("utf-8")


def serialize_markdown(result: dict[str, Any]) -> bytes:
    lines = ["| " + " | ".join(CSV_FIELDS) + " |", "|" + "|".join("---" for _ in CSV_FIELDS) + "|"]
    lines += ["| " + " | ".join(str(row[field]).replace("|", "\\|") for field in CSV_FIELDS) + " |" for row in _rows(result)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _expected_metadata(source: Source, key: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"metadata_schema_version": ARCHETYPE_METADATA_SCHEMA_VERSION, "completed": True, "archetype_cache_key": key,
            "analysis_identifier": ARCHETYPE_IDENTIFIER, "source_preprocessing_metadata_file": source.metadata_path.name,
            "source_preprocessing_data_file": source.data_path.name, "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
            "source_preprocessing_checksum": source.metadata["output_sha256"], "source_record_count": len(source.records),
            "missing_archetype_count": result["missing_archetype_count"], "included_record_count": result["included_record_count"],
            "metric_definition": METRIC_DEFINITION, "output_ordering_identifier": OUTPUT_ORDER, "output_formats": list(OUTPUT_FORMAT_ORDER)}


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle: return json.load(handle)


def valid_output(output: Path, key: str, source: Source, result: dict[str, Any]) -> bool:
    try:
        metadata = _read_json(output_metadata_path(output, key))
        if not isinstance(metadata, dict) or any(metadata.get(field) != value for field, value in _expected_metadata(source, key, result).items()): return False
        for name in OUTPUT_FORMAT_ORDER:
            checksum, size, filename = metadata.get(f"{name}_output_checksum"), metadata.get(f"{name}_output_file_size"), metadata.get(f"{name}_output_file")
            path = _safe_child(output, filename)
            if not isinstance(checksum, str) or type(size) is not int or filename != _output_path(output, key, checksum, name).name or path is None: return False
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum: return False
        return True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError): return False


def analyze_archetypes(input_metadata: Path, output: Path, *, force: bool = False, dry_run: bool = False,
                       writer: AtomicWriter = _write_atomic) -> dict[str, Any]:
    try: source = load_source(input_metadata)
    except RuntimeError as exc: raise ArchetypeError(str(exc)) from exc
    result = build_archetype_profiles(source.records)
    key = archetype_cache_key(source)
    metadata_path = output_metadata_path(output, key)
    hit = valid_output(output, key, source, result)
    plan = {"status": "planned", "cache_hit": hit, "archetype_cache_key": key, "source": source, "result": result, "output_metadata_path": metadata_path}
    if hit and not force and not dry_run:
        metadata = _read_json(metadata_path)
        return {**plan, "status": "cache_hit", "output_paths": {name: output / metadata[f"{name}_output_file"] for name in OUTPUT_FORMAT_ORDER}}
    if dry_run: return plan
    contents = {"json": serialize_json(result), "csv": serialize_csv(result), "markdown": serialize_markdown(result)}
    paths = {name: _output_path(output, key, hashlib.sha256(contents[name]).hexdigest(), name) for name in OUTPUT_FORMAT_ORDER}
    created: list[Path] = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_FORMAT_ORDER:
            if paths[name].exists():
                if not paths[name].is_file() or paths[name].read_bytes() != contents[name]: raise OSError("同名のarchetype generationが期待する内容と一致しません")
            else: writer(paths[name], contents[name]); created.append(paths[name])
        metadata = _expected_metadata(source, key, result)
        for name in OUTPUT_FORMAT_ORDER:
            metadata.update({f"{name}_output_file": paths[name].name, f"{name}_output_checksum": hashlib.sha256(contents[name]).hexdigest(), f"{name}_output_file_size": len(contents[name])})
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        for path in created: _best_effort_unlink(path)
        raise ArchetypeError("archetype profile出力の保存に失敗しました。既存出力は変更していません") from exc
    return {**plan, "status": "analyzed", "output_paths": paths}


def dry_run_lines(input_metadata: Path, output: Path, *, force: bool = False) -> list[str]:
    plan = analyze_archetypes(input_metadata, output, force=force, dry_run=True)
    return [
        f"input metadata path: {input_metadata}",
        f"included record count: {plan['result']['included_record_count']}",
        f"missing archetype count: {plan['result']['missing_archetype_count']}",
        f"output directory: {output}",
        f"archetype metadata path: {plan['output_metadata_path']}",
        f"archetype required: {'yes' if force or not plan['cache_hit'] else 'no'}",
    ]
