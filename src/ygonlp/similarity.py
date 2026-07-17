"""正規化済み効果テキストの決定論的な TF-IDF cosine 検索。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .measure import Source, _safe_child, _write_atomic, load_source

SIMILARITY_METADATA_SCHEMA_VERSION = 1
SIMILARITY_JSON_SCHEMA_VERSION = 1
SIMILARITY_CSV_SCHEMA_VERSION = 1
SIMILARITY_MARKDOWN_SCHEMA_VERSION = 1
KEY_PREFIX_LENGTH = 16
CONTENT_PREFIX_LENGTH = 16
TOKEN_PATTERN = r"(?u)\b[^\W_]+\b"
REPRESENTATION_IDENTIFIER = "sklearn_tfidf_word_unigram_l2_cosine_v1"
RANKING_IDENTIFIER = "positive_raw_cosine_desc_card_id_asc_v1"
OUTPUT_FORMAT_ORDER = ("json", "csv", "markdown")
OUTPUT_SUFFIXES = {"json": "json", "csv": "csv", "markdown": "md"}
CSV_FIELDS = ("card_id", "name", "card_type", "tcg_date", "release_status", "score")
VECTORIZER_PARAMETERS = {
    "lowercase": True,
    "token_pattern": TOKEN_PATTERN,
    "ngram_range": [1, 1],
    "norm": "l2",
    "use_idf": True,
    "smooth_idf": True,
    "sublinear_tf": False,
    "dtype": "float64",
}


class SimilarityError(RuntimeError):
    """類似検索入力または保存の Fatal error。"""


AtomicWriter = Callable[[Path, bytes], None]


def _release_status(record: dict[str, Any], today: date) -> str:
    if record["tcg_date"] is None:
        return "missing_date"
    return "future_dated" if record["tcg_date"] > today.isoformat() else "released"


def _validate_query(card_id: int | None, name: str | None, top_n: int, release_status: str | None) -> None:
    if (card_id is None) == (name is None):
        raise SimilarityError("queryはcard_idまたはnameのいずれか一方を指定してください")
    if type(top_n) is not int or top_n <= 0:
        raise SimilarityError("top_nは正の整数である必要があります")
    if release_status not in (None, "released", "missing_date", "future_dated"):
        raise SimilarityError("release_statusが不正です")


def _resolve_query(records: list[dict[str, Any]], card_id: int | None, name: str | None) -> dict[str, Any]:
    matches = (
        [record for record in records if record["card_id"] == card_id]
        if card_id is not None
        else [record for record in records if record["name"] == name]
    )
    if not matches:
        raise SimilarityError("query cardが見つかりません")
    if len(matches) != 1:
        raise SimilarityError("同名cardが複数あります。card_idを指定してください")
    query = matches[0]
    if not isinstance(query["text_normalized"], str) or not query["text_normalized"].strip():
        raise SimilarityError("query cardのtext_normalizedが空です")
    return query


def _vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(
        lowercase=True, token_pattern=TOKEN_PATTERN, ngram_range=(1, 1), norm="l2",
        use_idf=True, smooth_idf=True, sublinear_tf=False, dtype=np.float64,
    )


def search_records(
    records: Iterable[dict[str, Any]], *, card_id: int | None = None, name: str | None = None,
    top_n: int = 10, card_type: str | None = None, release_status: str | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """全 record から query を確定し、非空テキスト群だけで類似度を計算する。"""
    _validate_query(card_id, name, top_n, release_status)
    listed = list(records)
    query = _resolve_query(listed, card_id, name)
    cutoff = today or datetime.now(timezone.utc).date()
    corpus = [record for record in listed if isinstance(record["text_normalized"], str) and record["text_normalized"].strip()]
    try:
        matrix = _vectorizer().fit_transform([record["text_normalized"] for record in corpus])
    except ValueError as exc:
        raise SimilarityError("検索対象のtext_normalizedにtokenがありません") from exc
    query_index = corpus.index(query)
    if matrix[query_index].nnz == 0:
        raise SimilarityError("query cardのtext_normalizedにtokenがありません")
    scores = cosine_similarity(matrix[query_index], matrix).ravel()
    matches: list[tuple[float, dict[str, Any]]] = []
    for record, score in zip(corpus, scores):
        status = _release_status(record, cutoff)
        if (
            record["card_id"] == query["card_id"]
            or card_type is not None and record["card_type"] != card_type
            or release_status is not None and status != release_status
            or score <= 0
        ):
            continue
        matches.append((float(score), {
            "card_id": record["card_id"], "name": record["name"], "card_type": record["card_type"],
            "tcg_date": record["tcg_date"], "release_status": status,
        }))
    ranked = sorted(matches, key=lambda item: (-item[0], item[1]["card_id"]))[:top_n]
    return [{**record, "score": round(score, 6)} for score, record in ranked]


def _key_payload(source: Source, *, card_id: int | None, name: str | None, top_n: int, card_type: str | None, release_status: str | None, today: date) -> dict[str, Any]:
    return {
        "metadata_schema_version": SIMILARITY_METADATA_SCHEMA_VERSION,
        "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
        "source_preprocessing_checksum": source.metadata["output_sha256"],
        "query": {"card_id": card_id, "name": name}, "top_n": top_n,
        "filters": {"card_type": card_type, "release_status": release_status, "release_status_date": today.isoformat()},
        "representation_identifier": REPRESENTATION_IDENTIFIER, "ranking_identifier": RANKING_IDENTIFIER,
        "sklearn_version": sklearn.__version__, "vectorizer_class": "sklearn.feature_extraction.text.TfidfVectorizer",
        "vectorizer_parameters": VECTORIZER_PARAMETERS,
    }


def similarity_cache_key(source: Source, **kwargs: Any) -> str:
    payload = _key_payload(source, **kwargs)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _serialize_json(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _serialize_csv(result: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in result["matches"]:
        writer.writerow({**row, "score": f"{row['score']:.6f}"})
    return output.getvalue().encode("utf-8")


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _serialize_markdown(result: dict[str, Any]) -> bytes:
    lines = ["| " + " | ".join(CSV_FIELDS) + " |", "|" + "|".join("---" for _ in CSV_FIELDS) + "|"]
    lines.extend("| " + " | ".join(_markdown_cell(row[field]) for field in CSV_FIELDS) + " |" for row in result["matches"])
    return ("\n".join(lines) + "\n").encode("utf-8")


def output_metadata_path(output: Path, key: str) -> Path:
    return output / f"similarity-{key[:KEY_PREFIX_LENGTH]}.metadata.json"


def _output_path(output: Path, key: str, checksum: str, format_name: str) -> Path:
    return output / f"similarity-{key[:KEY_PREFIX_LENGTH]}-{checksum[:CONTENT_PREFIX_LENGTH]}.{OUTPUT_SUFFIXES[format_name]}"


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _expected_metadata(source: Source, key: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata_schema_version": SIMILARITY_METADATA_SCHEMA_VERSION, "completed": True,
        "similarity_cache_key": key, "source_preprocessing_metadata_file": source.metadata_path.name,
        "source_preprocessing_data_file": source.data_path.name,
        "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
        "source_preprocessing_checksum": source.metadata["output_sha256"],
        "source_record_count": len(source.records), "query": payload["query"], "filters": payload["filters"],
        "top_n": payload["top_n"], "ranking_identifier": RANKING_IDENTIFIER,
        "representation_identifier": REPRESENTATION_IDENTIFIER, "sklearn_version": sklearn.__version__,
        "vectorizer_class": payload["vectorizer_class"], "vectorizer_parameters": VECTORIZER_PARAMETERS,
        "result_count": len(result["matches"]), "output_formats": list(OUTPUT_FORMAT_ORDER),
    }


def valid_output(output: Path, key: str, source: Source, payload: dict[str, Any], result: dict[str, Any]) -> bool:
    try:
        metadata = _read_json(output_metadata_path(output, key))
        if not isinstance(metadata, dict) or any(metadata.get(field) != value for field, value in _expected_metadata(source, key, payload, result).items()):
            return False
        for format_name in OUTPUT_FORMAT_ORDER:
            checksum = metadata.get(f"{format_name}_output_checksum")
            size = metadata.get(f"{format_name}_output_file_size")
            filename = metadata.get(f"{format_name}_output_file")
            if not isinstance(checksum, str) or len(checksum) != 64 or type(size) is not int or size < 0:
                return False
            if filename != _output_path(output, key, checksum, format_name).name:
                return False
            path = _safe_child(output, filename)
            if path is None or not path.is_file():
                return False
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum or not raw.endswith(b"\n") or b"\r" in raw:
                return False
        return True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return False


def search_similar(input_metadata: Path, output: Path, *, card_id: int | None = None, name: str | None = None, top_n: int = 10, card_type: str | None = None, release_status: str | None = None, force: bool = False, today: date | None = None, writer: AtomicWriter = _write_atomic) -> dict[str, Any]:
    """検証済み前処理JSONLから検索し、metadataを最後にatomic commitする。"""
    source = load_source(input_metadata)
    _validate_query(card_id, name, top_n, release_status)
    cutoff = today or datetime.now(timezone.utc).date()
    payload = _key_payload(source, card_id=card_id, name=name, top_n=top_n, card_type=card_type, release_status=release_status, today=cutoff)
    key = similarity_cache_key(source, card_id=card_id, name=name, top_n=top_n, card_type=card_type, release_status=release_status, today=cutoff)
    query = _resolve_query(source.records, card_id, name)
    matches = search_records(source.records, card_id=card_id, name=name, top_n=top_n, card_type=card_type, release_status=release_status, today=cutoff)
    result = {"schema_version": SIMILARITY_JSON_SCHEMA_VERSION, "query": {"card_id": query["card_id"], "name": query["name"]}, "matches": matches}
    metadata_path = output_metadata_path(output, key)
    hit = valid_output(output, key, source, payload, result)
    if hit and not force:
        metadata = _read_json(metadata_path)
        return {"status": "cache_hit", "cache_hit": True, "similarity_cache_key": key, "source": source, "result": result, "output_metadata_path": metadata_path, "output_paths": {name: output / metadata[f"{name}_output_file"] for name in OUTPUT_FORMAT_ORDER}}
    contents = {"json": _serialize_json(result), "csv": _serialize_csv(result), "markdown": _serialize_markdown(result)}
    paths = {name: _output_path(output, key, hashlib.sha256(contents[name]).hexdigest(), name) for name in OUTPUT_FORMAT_ORDER}
    created: list[Path] = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_FORMAT_ORDER:
            if paths[name].exists():
                if not paths[name].is_file() or paths[name].read_bytes() != contents[name]:
                    raise OSError("同名のsimilarity generationが期待する内容と一致しません")
            else:
                writer(paths[name], contents[name])
                created.append(paths[name])
        metadata = _expected_metadata(source, key, payload, result)
        for name in OUTPUT_FORMAT_ORDER:
            metadata[f"{name}_output_file"] = paths[name].name
            metadata[f"{name}_output_checksum"] = hashlib.sha256(contents[name]).hexdigest()
            metadata[f"{name}_output_file_size"] = len(contents[name])
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise SimilarityError("類似検索出力の保存に失敗しました。既存出力は変更していません") from exc
    return {"status": "searched", "cache_hit": hit, "similarity_cache_key": key, "source": source, "result": result, "output_metadata_path": metadata_path, "output_paths": paths}
