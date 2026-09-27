"""Versioned local embeddings and deterministic semantic effect-text search."""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .artifacts import best_effort_unlink, read_json, safe_child, write_bytes_atomic
from .measure import load_source
from .semantic_backend import DEFAULT_SPEC, Embedder, EmbeddingSpec, Model2VecEmbedder


CORPUS_SCHEMA_VERSION = 1
QUERY_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
RANKING_IDENTIFIER = "cosine_raw_desc_card_id_asc_v1"
SELECTION_IDENTIFIER = "is_effect_text_target_and_nonblank_text_normalized_v1"
QUERY_NORMALIZATION = "collapse_unicode_whitespace_strip_v1"
Writer = Callable[[Path, bytes], None]
EmbedderFactory = Callable[[], Embedder]


class SemanticError(RuntimeError):
    """Invalid input, incompatible cache, or artifact failure."""


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _key(value: dict[str, Any]) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _paths(directory: Path, prefix: str, key: str, content: bytes, suffix: str) -> tuple[Path, Path]:
    stem = f"{prefix}-{key[:16]}"
    return (directory / f"{stem}-{_digest(content)[:16]}.{suffix}", directory / f"{stem}.metadata.json")


def _matrix_bytes(matrix: np.ndarray) -> bytes:
    stream = io.BytesIO()
    np.save(stream, np.asarray(matrix, dtype="<f4"), allow_pickle=False)
    return stream.getvalue()


def _matrix(raw: bytes, rows: int, dimension: int) -> np.ndarray:
    stream = io.BytesIO(raw)
    try:
        value = np.load(stream, allow_pickle=False)
    except (OSError, ValueError, EOFError) as exc:
        raise SemanticError("embedding artifact を読み込めません") from exc
    if (stream.tell() != len(raw) or value.shape != (rows, dimension)
            or value.dtype != np.dtype("float32") or not np.isfinite(value).all()
            or (rows and np.any(np.linalg.norm(value, axis=1) == 0))):
        raise SemanticError("embedding artifact の shape、型、または値が不正です")
    return value


def _encoded(embedder: Embedder, texts: list[str], dimension: int) -> np.ndarray:
    try:
        value = np.asarray(embedder.encode(texts), dtype=np.float32)
    except Exception as exc:
        raise SemanticError("embedding model の実行に失敗しました") from exc
    if value.shape != (len(texts), dimension) or not np.isfinite(value).all():
        raise SemanticError("embedding model が互換性のない shape または値を返しました")
    return value


def _publish(data_path: Path, metadata_path: Path, content: bytes,
             metadata: dict[str, Any], writer: Writer) -> None:
    created = False
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        if data_path.exists():
            if not data_path.is_file() or data_path.read_bytes() != content:
                raise OSError("同名の artifact generation が期待する内容と一致しません")
        else:
            writer(data_path, content)
            created = True
        writer(metadata_path, _json_bytes(metadata))
    except OSError as exc:
        if created:
            best_effort_unlink(data_path)
        raise SemanticError("artifact の保存に失敗しました。既存の有効な出力は保持されました") from exc


def _read_artifact(metadata_path: Path, prefix: str, key_field: str,
                   schema: int, expected: dict[str, Any] | None = None) -> tuple[dict[str, Any], bytes]:
    try:
        metadata = read_json(metadata_path)
        if (not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int
                or metadata["schema_version"] != schema or metadata.get("completed") is not True):
            raise SemanticError("artifact metadata の schema または完了状態が不正です")
        if expected is not None and any(metadata.get(k) != v for k, v in expected.items()):
            raise SemanticError("artifact metadata が現在の入力・設定と一致しません")
        key = metadata.get(key_field)
        checksum, size = metadata.get("data_sha256"), metadata.get("data_size")
        if (not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None
                or not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
                or type(size) is not int or size < 0):
            raise SemanticError("artifact metadata の key、checksum、size が不正です")
        expected_name = f"{prefix}-{key[:16]}-{checksum[:16]}.{metadata['data_format']}"
        if metadata_path.name != f"{prefix}-{key[:16]}.metadata.json" or metadata.get("data_file") != expected_name:
            raise SemanticError("artifact filename が metadata と一致しません")
        path = safe_child(metadata_path.parent, expected_name)
        if path is None or not path.is_file():
            raise SemanticError("artifact data file がありません")
        raw = path.read_bytes()
        if len(raw) != size or _digest(raw) != checksum:
            raise SemanticError("artifact checksum または size が一致しません")
        return metadata, raw
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise SemanticError("artifact を検証できません") from exc


def _corpus_key_payload(source: Any, metadata_sha: str, spec: EmbeddingSpec) -> dict[str, Any]:
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "source_preprocessing_metadata_sha256": metadata_sha,
        "source_preprocessing_data_sha256": source.metadata["output_sha256"],
        "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
        "model": spec.metadata(), "text_field": "text_normalized",
        "selection": SELECTION_IDENTIFIER,
    }


def _valid_corpus(path: Path, payload: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray] | None:
    try:
        metadata, raw = _read_artifact(path, "effect-embeddings", "corpus_cache_key", CORPUS_SCHEMA_VERSION, payload)
        if metadata.get("corpus_cache_key") != _key(payload):
            return None
        cards = metadata.get("cards")
        counts = ("source_record_count", "eligible_count", "embedded_count",
                  "excluded_non_target_count", "empty_text_count", "zero_vector_count")
        if (not isinstance(cards, list) or any(type(metadata.get(name)) is not int or metadata[name] < 0 for name in counts)
                or len(cards) != metadata["embedded_count"] or metadata.get("data_format") != "npy"
                or metadata["source_record_count"] != metadata["eligible_count"] + metadata["excluded_non_target_count"] + metadata["empty_text_count"]
                or metadata["eligible_count"] != metadata["embedded_count"] + metadata["zero_vector_count"]):
            return None
        ids = [card.get("card_id") for card in cards if isinstance(card, dict)]
        if (len(ids) != len(cards) or any(type(card_id) is not int for card_id in ids)
                or ids != sorted(set(ids)) or any(
                    not isinstance(card.get("name"), str) or not isinstance(card.get("card_type"), str)
                    or card.get("tcg_date") is not None and not isinstance(card.get("tcg_date"), str)
                    for card in cards
                )):
            return None
        return metadata, _matrix(raw, len(cards), payload["model"]["dimension"])
    except SemanticError:
        return None


def embed_effect_text(input_metadata: Path, output: Path, *, force: bool = False,
                      spec: EmbeddingSpec = DEFAULT_SPEC,
                      embedder_factory: EmbedderFactory = Model2VecEmbedder,
                      writer: Writer = write_bytes_atomic) -> dict[str, Any]:
    """Check the complete corpus cache before constructing the model."""
    try:
        source = load_source(input_metadata)
        metadata_sha = _digest(input_metadata.read_bytes())
    except (OSError, RuntimeError) as exc:
        raise SemanticError("前処理入力を検証できません") from exc
    payload = _corpus_key_payload(source, metadata_sha, spec)
    key = _key(payload)
    metadata_path = output / f"effect-embeddings-{key[:16]}.metadata.json"
    hit = _valid_corpus(metadata_path, payload)
    if hit is not None and not force:
        metadata, _ = hit
        return {"status": "cache_hit", "metadata_path": metadata_path,
                "data_path": output / metadata["data_file"], "metadata": metadata}

    eligible = [record for record in source.records if record["is_effect_text_target"]
                and isinstance(record["text_normalized"], str) and record["text_normalized"].strip()]
    empty_text = sum(not isinstance(record["text_normalized"], str)
                     or not record["text_normalized"].strip() for record in source.records)
    excluded_non_target = len(source.records) - empty_text - len(eligible)
    vectors = _encoded(embedder_factory(), [record["text_normalized"] for record in eligible], spec.dimension) if eligible else np.empty((0, spec.dimension), dtype=np.float32)
    keep = np.linalg.norm(vectors, axis=1) > 0
    cards = [{field: record[field] for field in ("card_id", "name", "card_type", "tcg_date")}
             for record, accepted in zip(eligible, keep) if accepted]
    matrix = vectors[keep]
    content = _matrix_bytes(matrix)
    data_path, metadata_path = _paths(output, "effect-embeddings", key, content, "npy")
    metadata = {
        **payload, "completed": True, "corpus_cache_key": key,
        "source_preprocessing_metadata_file": input_metadata.name,
        "source_preprocessing_data_file": source.data_path.name,
        "source_record_count": len(source.records), "eligible_count": len(eligible),
        "embedded_count": len(cards), "excluded_non_target_count": excluded_non_target,
        "empty_text_count": empty_text, "zero_vector_count": int(len(eligible) - len(cards)),
        "cards": cards, "data_format": "npy", "data_file": data_path.name,
        "data_sha256": _digest(content), "data_size": len(content),
    }
    _publish(data_path, metadata_path, content, metadata, writer)
    return {"status": "embedded", "metadata_path": metadata_path,
            "data_path": data_path, "metadata": metadata}


def _load_corpus(metadata_path: Path, spec: EmbeddingSpec) -> tuple[dict[str, Any], np.ndarray]:
    metadata, _ = _read_artifact(metadata_path, "effect-embeddings", "corpus_cache_key", CORPUS_SCHEMA_VERSION)
    payload = {field: metadata.get(field) for field in (
        "schema_version", "source_preprocessing_metadata_sha256", "source_preprocessing_data_sha256",
        "source_preprocessing_cache_key", "model", "text_field", "selection")}
    if metadata.get("model") != spec.metadata() or metadata.get("corpus_cache_key") != _key(payload):
        raise SemanticError("embedding corpus の model または cache key が互換ではありません")
    valid = _valid_corpus(metadata_path, payload)
    if valid is None:
        raise SemanticError("embedding corpus が破損または非互換です")
    return valid


def normalize_query(query: str) -> str:
    normalized = re.sub(r"\s+", " ", query).strip()
    if not normalized:
        raise SemanticError("query は空にできません")
    return normalized


def _query_embedding(query: str, output: Path, spec: EmbeddingSpec, *, offline: bool,
                     force: bool, embedder_factory: EmbedderFactory, writer: Writer) -> tuple[np.ndarray, dict[str, Any], Path]:
    normalized = normalize_query(query)
    payload = {"schema_version": QUERY_SCHEMA_VERSION, "model": spec.metadata(),
               "normalized_query": normalized, "query_normalization": QUERY_NORMALIZATION}
    key = _key(payload)
    directory = output / "query-embeddings"
    metadata_path = directory / f"query-embedding-{key[:16]}.metadata.json"
    try:
        metadata, raw = _read_artifact(metadata_path, "query-embedding", "query_cache_key", QUERY_SCHEMA_VERSION, payload)
        vector = _matrix(raw, 1, spec.dimension)[0] if metadata.get("data_format") == "npy" and metadata.get("query_cache_key") == key else None
    except SemanticError:
        metadata, vector = None, None
    if vector is not None and (offline or not force):
        return vector, metadata, metadata_path
    if offline:
        raise SemanticError("offline query embedding cache miss: 互換性のある query embedding がありません")
    vector = _encoded(embedder_factory(), [normalized], spec.dimension)[0]
    if not np.linalg.norm(vector):
        raise SemanticError("query の embedding が空です")
    content = _matrix_bytes(vector.reshape(1, -1))
    data_path, metadata_path = _paths(directory, "query-embedding", key, content, "npy")
    metadata = {**payload, "completed": True, "query_cache_key": key,
                "first_exact_query": query, "data_format": "npy", "data_file": data_path.name,
                "data_sha256": _digest(content), "data_size": len(content)}
    _publish(data_path, metadata_path, content, metadata, writer)
    return vector, metadata, metadata_path


def search_semantic(embedding_metadata: Path, output: Path, *, card_id: int | None = None,
                    query: str | None = None, top_n: int = 10, offline: bool = False,
                    force: bool = False, spec: EmbeddingSpec = DEFAULT_SPEC,
                    embedder_factory: EmbedderFactory = Model2VecEmbedder,
                    writer: Writer = write_bytes_atomic) -> dict[str, Any]:
    if (card_id is None) == (query is None):
        raise SemanticError("card_id または query のどちらか一方を指定してください")
    if type(top_n) is not int or top_n <= 0:
        raise SemanticError("top_n は正の整数である必要があります")
    metadata, matrix = _load_corpus(embedding_metadata, spec)
    cards = metadata["cards"]
    if card_id is not None:
        if type(card_id) is not int:
            raise SemanticError("card_id が不正です")
        positions = [index for index, card in enumerate(cards) if card["card_id"] == card_id]
        if not positions:
            raise SemanticError("指定した card_id は embedding corpus にありません")
        query_vector = matrix[positions[0]]
        query_info = {"kind": "card_id", "value": card_id}
        query_key = None
        query_metadata = None
    else:
        query_vector, query_metadata, _ = _query_embedding(
            query, output, spec, offline=offline, force=force,
            embedder_factory=embedder_factory, writer=writer)
        query_info = {"kind": "text", "value": query,
                      "normalized_value": normalize_query(query)}
        query_key = query_metadata["query_cache_key"]

    if not len(cards):
        ranked: list[tuple[float, dict[str, Any]]] = []
    else:
        denominators = np.linalg.norm(matrix.astype(np.float64), axis=1) * np.linalg.norm(query_vector.astype(np.float64))
        scores = matrix.astype(np.float64) @ query_vector.astype(np.float64) / denominators
        ranked = sorted(((float(score), card) for score, card in zip(scores, cards)
                         if card_id is None or card["card_id"] != card_id),
                        key=lambda pair: (-pair[0], pair[1]["card_id"]))[:top_n]
    matches = [{**card, "score": round(score, 6)} for score, card in ranked]
    result_payload = {"schema_version": RESULT_SCHEMA_VERSION,
                      "corpus_cache_key": metadata["corpus_cache_key"],
                      "corpus_data_sha256": metadata["data_sha256"],
                      "query": query_info, "query_embedding_cache_key": query_key,
                      "top_n": top_n, "ranking_identifier": RANKING_IDENTIFIER}
    key = _key(result_payload)
    result = {"schema_version": RESULT_SCHEMA_VERSION,
              "result_cache_key": key, "corpus_cache_key": metadata["corpus_cache_key"],
              "source_preprocessing_metadata_sha256": metadata["source_preprocessing_metadata_sha256"],
              "source_preprocessing_data_sha256": metadata["source_preprocessing_data_sha256"],
              "model": spec.metadata(), "query": query_info,
              "query_embedding_cache_key": query_key,
              "query_embedding_data_sha256": query_metadata["data_sha256"] if query_metadata else None,
              "top_n": top_n, "ranking_identifier": RANKING_IDENTIFIER, "matches": matches}
    metadata_path = output / f"semantic-search-{key[:16]}.metadata.json"
    try:
        prior, prior_raw = _read_artifact(metadata_path, "semantic-search", "result_cache_key", RESULT_SCHEMA_VERSION, result_payload)
        hit = prior.get("data_format") == "json" and prior_raw == _json_bytes(result)
    except SemanticError:
        hit = False
    if hit and not force:
        return {"status": "cache_hit", "metadata_path": metadata_path,
                "data_path": output / prior["data_file"], "result": result}
    content = _json_bytes(result)
    data_path, metadata_path = _paths(output, "semantic-search", key, content, "json")
    result_metadata = {**result_payload, "completed": True, "result_cache_key": key,
                       "source_preprocessing_metadata_sha256": metadata["source_preprocessing_metadata_sha256"],
                       "source_preprocessing_data_sha256": metadata["source_preprocessing_data_sha256"],
                       "model": spec.metadata(), "query_embedding_data_sha256":
                       query_metadata["data_sha256"] if query_metadata else None,
                       "result_count": len(matches), "data_format": "json", "data_file": data_path.name,
                       "data_sha256": _digest(content), "data_size": len(content)}
    _publish(data_path, metadata_path, content, result_metadata, writer)
    return {"status": "searched", "metadata_path": metadata_path,
            "data_path": data_path, "result": result}
