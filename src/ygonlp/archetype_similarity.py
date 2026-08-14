"""archetype内の効果テキスト類似ペアを決定論的に分析・保存する。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import sklearn
from sklearn.metrics.pairwise import cosine_similarity

from .artifacts import read_json as _read_json
from .measure import Source, _best_effort_unlink, _safe_child, _write_atomic, load_source
from .similarity import REPRESENTATION_IDENTIFIER, VECTORIZER_PARAMETERS, _vectorizer

ARCHETYPE_SIMILARITY_METADATA_SCHEMA_VERSION = 1
ARCHETYPE_SIMILARITY_JSON_SCHEMA_VERSION = 1
ARCHETYPE_SIMILARITY_IDENTIFIER = "archetype_text_similarity_v1"
RANKING_IDENTIFIER = "positive_raw_cosine_desc_card_id_pair_asc_top_n_per_archetype_v1"
OUTPUT_FORMAT_ORDER = ("json", "csv", "markdown")
OUTPUT_SUFFIXES = {"json": "json", "csv": "csv", "markdown": "md"}
CSV_FIELDS = ("archetype", "left_card_id", "left_name", "left_card_type", "right_card_id", "right_name", "right_card_type", "score")
AtomicWriter = Callable[[Path, bytes], None]


class ArchetypeSimilarityError(RuntimeError):
    """archetype類似性分析の入力または保存に失敗した。"""


def _validate_top_n(top_n: int) -> None:
    if type(top_n) is not int or top_n <= 0:
        raise ArchetypeSimilarityError("top_nは正の整数である必要があります")


def build_archetype_similarities(records: Iterable[dict[str, Any]], *, top_n: int = 10) -> dict[str, Any]:
    """archetypeごとに、効果テキストを持つカードの正の類似ペア上位を返す。"""
    _validate_top_n(top_n)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    archetypes_with_metadata: set[str] = set()
    missing_archetype_count = excluded_non_target_count = empty_text_count = 0
    for record in records:
        archetype = record["archetype"]
        if not isinstance(archetype, str) or not archetype:
            missing_archetype_count += 1
            continue
        archetypes_with_metadata.add(archetype)
        if not isinstance(record["text_normalized"], str) or not record["text_normalized"].strip():
            empty_text_count += 1
        elif not record["is_effect_text_target"]:
            excluded_non_target_count += 1
        else:
            groups[archetype].append(record)

    archetypes = []
    insufficient_candidate_archetype_count = 0
    for archetype in sorted(archetypes_with_metadata):
        cards = sorted(groups.get(archetype, []), key=lambda record: record["card_id"])
        if len(cards) < 2:
            insufficient_candidate_archetype_count += 1
            continue
        try:
            matrix = _vectorizer().fit_transform([card["text_normalized"] for card in cards])
        except ValueError as exc:
            raise ArchetypeSimilarityError("archetype内のtext_normalizedにtokenがありません") from exc
        scores = cosine_similarity(matrix)
        pairs: list[tuple[float, dict[str, Any]]] = []
        for left_index, left in enumerate(cards):
            for right_index in range(left_index + 1, len(cards)):
                score = float(scores[left_index, right_index])
                if score <= 0:
                    continue
                right = cards[right_index]
                pairs.append((score, {
                    "left_card_id": left["card_id"], "left_name": left["name"], "left_card_type": left["card_type"],
                    "right_card_id": right["card_id"], "right_name": right["name"], "right_card_type": right["card_type"],
                }))
        ranked = sorted(pairs, key=lambda item: (-item[0], item[1]["left_card_id"], item[1]["right_card_id"]))[:top_n]
        archetypes.append({
            "archetype": archetype,
            "candidate_card_count": len(cards),
            "positive_pair_count": len(pairs),
            "matches": [{**pair, "score": round(score, 6)} for score, pair in ranked],
        })
    return {
        "schema_version": ARCHETYPE_SIMILARITY_JSON_SCHEMA_VERSION,
        "analysis_identifier": ARCHETYPE_SIMILARITY_IDENTIFIER,
        "representation_identifier": REPRESENTATION_IDENTIFIER,
        "ranking_identifier": RANKING_IDENTIFIER,
        "top_n_per_archetype": top_n,
        "missing_archetype_count": missing_archetype_count,
        "excluded_non_target_count": excluded_non_target_count,
        "empty_text_count": empty_text_count,
        "insufficient_candidate_archetype_count": insufficient_candidate_archetype_count,
        "archetypes": archetypes,
    }


def _key_payload(source: Source, top_n: int) -> dict[str, Any]:
    return {
        "metadata_schema_version": ARCHETYPE_SIMILARITY_METADATA_SCHEMA_VERSION,
        "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
        "source_preprocessing_checksum": source.metadata["output_sha256"],
        "top_n_per_archetype": top_n,
        "representation_identifier": REPRESENTATION_IDENTIFIER,
        "ranking_identifier": RANKING_IDENTIFIER,
        "sklearn_version": sklearn.__version__,
        "vectorizer_class": "sklearn.feature_extraction.text.TfidfVectorizer",
        "vectorizer_parameters": VECTORIZER_PARAMETERS,
    }


def archetype_similarity_cache_key(source: Source, *, top_n: int) -> str:
    return hashlib.sha256(json.dumps(_key_payload(source, top_n), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def output_metadata_path(output: Path, key: str) -> Path:
    return output / f"archetype-similarity-{key[:16]}.metadata.json"


def _output_path(output: Path, key: str, checksum: str, format_name: str) -> Path:
    return output / f"archetype-similarity-{key[:16]}-{checksum[:16]}.{OUTPUT_SUFFIXES[format_name]}"


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"archetype": profile["archetype"], **match} for profile in result["archetypes"] for match in profile["matches"]]


def _serialize_json(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _serialize_csv(result: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in _rows(result):
        writer.writerow({**row, "score": f"{row['score']:.6f}"})
    return output.getvalue().encode("utf-8")


def _cell(value: Any) -> str:
    return f"{value:.6f}" if isinstance(value, float) else str(value).replace("|", "\\|").replace("\n", "<br>")


def _serialize_markdown(result: dict[str, Any]) -> bytes:
    lines = ["| " + " | ".join(CSV_FIELDS) + " |", "|" + "|".join("---" for _ in CSV_FIELDS) + "|"]
    lines.extend("| " + " | ".join(_cell(row[field]) for field in CSV_FIELDS) + " |" for row in _rows(result))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _expected_metadata(source: Source, key: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata_schema_version": ARCHETYPE_SIMILARITY_METADATA_SCHEMA_VERSION, "completed": True,
        "archetype_similarity_cache_key": key, "source_preprocessing_metadata_file": source.metadata_path.name,
        "source_preprocessing_data_file": source.data_path.name, "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
        "source_preprocessing_checksum": source.metadata["output_sha256"], "source_record_count": len(source.records),
        **payload, "missing_archetype_count": result["missing_archetype_count"],
        "excluded_non_target_count": result["excluded_non_target_count"], "empty_text_count": result["empty_text_count"],
        "insufficient_candidate_archetype_count": result["insufficient_candidate_archetype_count"],
        "analyzed_archetype_count": len(result["archetypes"]), "output_formats": list(OUTPUT_FORMAT_ORDER),
    }


def valid_output(output: Path, key: str, source: Source, payload: dict[str, Any], result: dict[str, Any]) -> bool:
    try:
        metadata = _read_json(output_metadata_path(output, key))
        if not isinstance(metadata, dict) or any(metadata.get(field) != value for field, value in _expected_metadata(source, key, payload, result).items()):
            return False
        for name in OUTPUT_FORMAT_ORDER:
            checksum, size, filename = metadata.get(f"{name}_output_checksum"), metadata.get(f"{name}_output_file_size"), metadata.get(f"{name}_output_file")
            path = _safe_child(output, filename)
            if not isinstance(checksum, str) or type(size) is not int or filename != _output_path(output, key, checksum, name).name or path is None:
                return False
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum:
                return False
        return True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return False


def analyze_archetype_similarity(input_metadata: Path, output: Path, *, top_n: int = 10, force: bool = False,
                                 dry_run: bool = False, writer: AtomicWriter = _write_atomic) -> dict[str, Any]:
    try:
        source = load_source(input_metadata)
    except RuntimeError as exc:
        raise ArchetypeSimilarityError(str(exc)) from exc
    _validate_top_n(top_n)
    result = build_archetype_similarities(source.records, top_n=top_n)
    payload = _key_payload(source, top_n)
    key = archetype_similarity_cache_key(source, top_n=top_n)
    metadata_path = output_metadata_path(output, key)
    hit = valid_output(output, key, source, payload, result)
    plan = {"status": "planned", "cache_hit": hit, "archetype_similarity_cache_key": key, "source": source, "result": result, "output_metadata_path": metadata_path}
    if hit and not force and not dry_run:
        metadata = _read_json(metadata_path)
        return {**plan, "status": "cache_hit", "output_paths": {name: output / metadata[f"{name}_output_file"] for name in OUTPUT_FORMAT_ORDER}}
    if dry_run:
        return plan
    contents = {"json": _serialize_json(result), "csv": _serialize_csv(result), "markdown": _serialize_markdown(result)}
    paths = {name: _output_path(output, key, hashlib.sha256(contents[name]).hexdigest(), name) for name in OUTPUT_FORMAT_ORDER}
    created: list[Path] = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_FORMAT_ORDER:
            if paths[name].exists():
                if not paths[name].is_file() or paths[name].read_bytes() != contents[name]:
                    raise OSError("同名のarchetype similarity出力が期待する内容と一致しません")
            else:
                writer(paths[name], contents[name]); created.append(paths[name])
        metadata = _expected_metadata(source, key, payload, result)
        for name in OUTPUT_FORMAT_ORDER:
            metadata.update({f"{name}_output_file": paths[name].name, f"{name}_output_checksum": hashlib.sha256(contents[name]).hexdigest(), f"{name}_output_file_size": len(contents[name])})
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        for path in created:
            _best_effort_unlink(path)
        raise ArchetypeSimilarityError("archetype similarity出力の保存に失敗しました。既存出力は変更していません") from exc
    return {**plan, "status": "analyzed", "output_paths": paths}


def dry_run_lines(input_metadata: Path, output: Path, *, top_n: int = 10, force: bool = False) -> list[str]:
    plan = analyze_archetype_similarity(input_metadata, output, top_n=top_n, force=force, dry_run=True)
    return [
        f"input metadata path: {input_metadata}", f"top n per archetype: {top_n}",
        f"analyzed archetype count: {len(plan['result']['archetypes'])}", f"output directory: {output}",
        f"archetype similarity metadata path: {plan['output_metadata_path']}",
        f"archetype similarity required: {'yes' if force or not plan['cache_hit'] else 'no'}",
    ]
