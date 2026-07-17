"""検証済み前処理JSONLの決定論的な語彙・探索的トピック分析。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import sklearn
from sklearn.decomposition import LatentDirichletAllocation
from sklearn.feature_extraction.text import CountVectorizer

from .measure import Source, _safe_child, _write_atomic, load_source

FLOAT_PRECISION = 6
KEY_PREFIX_LENGTH = CONTENT_PREFIX_LENGTH = 16
OUTPUT_FORMAT_ORDER = ("json", "csv", "markdown")
OUTPUT_SUFFIXES = {"json": "json", "csv": "csv", "markdown": "md"}
TOKEN_PATTERN = r"(?u)\b[^\W_]+\b"
VOCABULARY_RANKING = "corpus_frequency_desc_document_frequency_desc_term_asc_raw_v1"
TOPIC_TERM_RANKING = "raw_component_weight_desc_term_asc_v1"
REPRESENTATIVE_RANKING = "raw_document_topic_proportion_desc_card_id_asc_v1"
TOPIC_ORDERING = "model_topic_index_ascending_v1"
VOCABULARY_IDENTIFIER = "count_vectorizer_vocabulary_v1"
TOPICS_IDENTIFIER = "count_vectorizer_lda_topics_v1"
VOCABULARY_METADATA_SCHEMA_VERSION = 2
VOCABULARY_JSON_SCHEMA_VERSION = 1
VOCABULARY_CSV_SCHEMA_VERSION = 1
VOCABULARY_MARKDOWN_SCHEMA_VERSION = 1
TOPICS_METADATA_SCHEMA_VERSION = 2
TOPICS_JSON_SCHEMA_VERSION = 1
TOPICS_CSV_SCHEMA_VERSION = 1
TOPICS_MARKDOWN_SCHEMA_VERSION = 1
VOCABULARY_OUTPUT_ORDERING = "vocabulary_corpus_frequency_desc_document_frequency_desc_term_asc_v1"
TOPICS_OUTPUT_ORDERING = "topic_terms_topic_index_asc_then_representative_cards_topic_index_asc_then_documents_source_order_topic_index_asc_then_prevalence_overall_card_type_asc_tcg_year_asc_topic_index_asc_v1"


class VocabularyError(RuntimeError):
    """語彙・トピック分析の入力または保存エラー。"""


AtomicWriter = Callable[[Path, bytes], None]


def _rounded(value: float) -> float:
    result = round(float(value), FLOAT_PRECISION)
    return 0.0 if result == 0 else result


def _validate_positive(value: Any, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise VocabularyError(f"{label}は正の整数である必要があります")


def _validate_common(min_df: int, english_stopwords: bool) -> None:
    _validate_positive(min_df, "min_df")
    if type(english_stopwords) is not bool:
        raise VocabularyError("english_stopwordsはboolである必要があります")


def _vectorizer(*, ngram: int, min_df: int, english_stopwords: bool) -> CountVectorizer:
    return CountVectorizer(
        lowercase=True, token_pattern=TOKEN_PATTERN, ngram_range=(ngram, ngram),
        min_df=min_df, stop_words="english" if english_stopwords else None, dtype=np.int64,
    )


def _vectorizer_parameters(*, ngram: int, min_df: int, english_stopwords: bool) -> dict[str, Any]:
    return {"lowercase": True, "token_pattern": TOKEN_PATTERN, "ngram_range": [ngram, ngram],
            "min_df": min_df, "stop_words": "english" if english_stopwords else None, "dtype": "int64"}


def _documents(source: Source, *, ngram: int, min_df: int, english_stopwords: bool) -> tuple[list[dict[str, Any]], list[str], int, int]:
    analyzer = _vectorizer(ngram=ngram, min_df=min_df, english_stopwords=english_stopwords).build_analyzer()
    included: list[dict[str, Any]] = []
    empty = tokenless = 0
    for record in source.records:
        text = record["text_normalized"]
        if not isinstance(text, str) or not text.strip():
            empty += 1
        elif not analyzer(text):
            tokenless += 1
        else:
            included.append(record)
    if not included:
        raise VocabularyError("空またはtokenなし以外の文書がありません")
    return included, [record["text_normalized"] for record in included], empty, tokenless


def _fit_matrix(texts: list[str], *, ngram: int, min_df: int, english_stopwords: bool) -> tuple[CountVectorizer, Any]:
    vectorizer = _vectorizer(ngram=ngram, min_df=min_df, english_stopwords=english_stopwords)
    try:
        return vectorizer, vectorizer.fit_transform(texts)
    except ValueError as exc:
        raise VocabularyError("vectorizerの語彙が空です。min_dfまたはstopword設定を確認してください") from exc


def build_vocabulary(source: Source, *, ngram: int = 1, min_df: int = 1, english_stopwords: bool = False) -> dict[str, Any]:
    if ngram not in (1, 2):
        raise VocabularyError("ngramは1または2である必要があります")
    _validate_common(min_df, english_stopwords)
    records, texts, empty, tokenless = _documents(source, ngram=ngram, min_df=min_df, english_stopwords=english_stopwords)
    vectorizer, matrix = _fit_matrix(texts, ngram=ngram, min_df=min_df, english_stopwords=english_stopwords)
    frequencies = np.asarray(matrix.sum(axis=0)).ravel()
    document_frequencies = np.asarray(matrix.getnnz(axis=0)).ravel()
    terms = vectorizer.get_feature_names_out()
    ranked = sorted(zip(terms, frequencies, document_frequencies), key=lambda item: (-int(item[1]), -int(item[2]), item[0]))
    return {
        "schema_version": VOCABULARY_JSON_SCHEMA_VERSION, "analysis_identifier": VOCABULARY_IDENTIFIER, "ngram": ngram,
        "document_count": len(records), "excluded_empty_document_count": empty, "excluded_tokenless_document_count": tokenless,
        "vectorizer_parameters": _vectorizer_parameters(ngram=ngram, min_df=min_df, english_stopwords=english_stopwords),
        "ranking_identifier": VOCABULARY_RANKING,
        "terms": [{"term": term, "corpus_frequency": int(frequency), "document_frequency": int(df),
                   "document_frequency_ratio": _rounded(int(df) / len(records))} for term, frequency, df in ranked],
    }


def _release_groups(records: list[dict[str, Any]], proportions: np.ndarray, cutoff: date) -> dict[str, Any]:
    groups: dict[tuple[str, str | None], list[np.ndarray]] = defaultdict(list)
    missing = future = 0
    for record, row in zip(records, proportions):
        groups[("overall", None)].append(row)
        groups[("card_type", record["card_type"])].append(row)
        if record["tcg_date"] is None:
            missing += 1
        elif record["tcg_date"] > cutoff.isoformat():
            future += 1
        else:
            groups[("tcg_year", record["tcg_date"][:4])].append(row)
    prevalence = []
    order = [("overall", None), *[("card_type", key) for key in sorted(k for s, k in groups if s == "card_type")],
             *[("tcg_year", key) for key in sorted(k for s, k in groups if s == "tcg_year")]]
    for scope, group in order:
        values = groups[(scope, group)]
        prevalence.append({"scope": scope, "group": group, "document_count": len(values),
                           "topic_prevalence": [_rounded(value) for value in np.mean(np.asarray(values), axis=0)]})
    return {"prevalence": prevalence, "missing_date_count": missing, "future_date_count": future}


def build_topics(source: Source, *, topic_count: int = 5, top_terms: int = 10, representative_cards: int = 10,
                 random_seed: int = 0, max_iter: int = 10, min_df: int = 1, english_stopwords: bool = False,
                 cutoff: date | None = None) -> dict[str, Any]:
    for value, label in ((topic_count, "topic_count"), (top_terms, "top_terms"), (representative_cards, "representative_cards"), (max_iter, "max_iter")):
        _validate_positive(value, label)
    if type(random_seed) is not int:
        raise VocabularyError("random_seedは整数である必要があります")
    _validate_common(min_df, english_stopwords)
    records, texts, empty, tokenless = _documents(source, ngram=1, min_df=min_df, english_stopwords=english_stopwords)
    vectorizer, matrix = _fit_matrix(texts, ngram=1, min_df=min_df, english_stopwords=english_stopwords)
    model = LatentDirichletAllocation(n_components=topic_count, max_iter=max_iter, learning_method="batch", random_state=random_seed)
    try:
        proportions = model.fit_transform(matrix)
    except ValueError as exc:
        raise VocabularyError("LDAの学習に失敗しました") from exc
    terms = vectorizer.get_feature_names_out()
    topics = []
    for index, component in enumerate(model.components_):
        total = float(component.sum())
        ranked_terms = sorted(zip(terms, component), key=lambda item: (-float(item[1]), item[0]))[:top_terms]
        ranked_cards = sorted(enumerate(proportions[:, index]), key=lambda item: (-float(item[1]), records[item[0]]["card_id"]))[:representative_cards]
        topics.append({"topic_index": index,
                       "top_terms": [{"term": term, "weight": _rounded(float(weight) / total)} for term, weight in ranked_terms],
                       "representative_cards": [{"card_id": records[row]["card_id"], "name": records[row]["name"],
                                                  "card_type": records[row]["card_type"], "tcg_date": records[row]["tcg_date"],
                                                  "proportion": _rounded(float(proportion))} for row, proportion in ranked_cards]})
    cutoff = cutoff or datetime.now(timezone.utc).date()
    grouped = _release_groups(records, proportions, cutoff)
    return {"schema_version": TOPICS_JSON_SCHEMA_VERSION, "analysis_identifier": TOPICS_IDENTIFIER, "topic_ordering_identifier": TOPIC_ORDERING,
            "topic_count": topic_count, "document_count": len(records), "excluded_empty_document_count": empty,
            "excluded_tokenless_document_count": tokenless, "missing_date_count": grouped["missing_date_count"],
            "future_date_count": grouped["future_date_count"], "current_date_cutoff": cutoff.isoformat(),
            "vectorizer_parameters": _vectorizer_parameters(ngram=1, min_df=min_df, english_stopwords=english_stopwords),
            "lda_parameters": {"n_components": topic_count, "max_iter": max_iter, "learning_method": "batch", "random_state": random_seed},
            "term_ranking_identifier": TOPIC_TERM_RANKING, "representative_card_ranking_identifier": REPRESENTATIVE_RANKING,
            "topics": topics,
            "documents": [{"card_id": record["card_id"], "name": record["name"], "card_type": record["card_type"], "tcg_date": record["tcg_date"],
                           "topic_proportions": [_rounded(float(value)) for value in row]} for record, row in zip(records, proportions)],
            "topic_prevalence": grouped["prevalence"]}


def _rows(result: dict[str, Any], kind: str) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    if kind == "vocabulary":
        fields = ("term", "corpus_frequency", "document_frequency", "document_frequency_ratio")
        return fields, result["terms"]
    fields = ("record_type", "topic_index", "term", "card_id", "name", "card_type", "tcg_date", "scope", "group", "document_count", "value")
    rows: list[dict[str, Any]] = []
    for topic in result["topics"]:
        rows += [{"record_type": "topic_term", "topic_index": topic["topic_index"], "term": term["term"], "value": term["weight"]} for term in topic["top_terms"]]
    for topic in result["topics"]:
        rows += [{"record_type": "representative_card", "topic_index": topic["topic_index"], **card, "value": card["proportion"]} for card in topic["representative_cards"]]
    for document in result["documents"]:
        rows += [{"record_type": "document_topic", "topic_index": index, "card_id": document["card_id"], "name": document["name"], "card_type": document["card_type"], "tcg_date": document["tcg_date"], "value": value} for index, value in enumerate(document["topic_proportions"])]
    for group in result["topic_prevalence"]:
        rows += [{"record_type": "topic_prevalence", "topic_index": index, "scope": group["scope"], "group": group["group"], "document_count": group["document_count"], "value": value} for index, value in enumerate(group["topic_prevalence"])]
    return fields, rows


def _serialize_json(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _serialize_csv(result: dict[str, Any], kind: str) -> bytes:
    fields, rows = _rows(result, kind); output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n"); writer.writeheader()
    for row in rows:
        writer.writerow({field: f"{row[field]:.{FLOAT_PRECISION}f}" if isinstance(row.get(field), float) else ("" if row.get(field) is None else row.get(field, "")) for field in fields})
    return output.getvalue().encode("utf-8")


def _serialize_markdown(result: dict[str, Any], kind: str) -> bytes:
    fields, rows = _rows(result, kind); lines = ["| " + " | ".join(fields) + " |", "|" + "|".join("---" for _ in fields) + "|"]
    for row in rows:
        values = []
        for field in fields:
            value = row.get(field)
            rendered = "—" if value is None else (f"{value:.{FLOAT_PRECISION}f}" if isinstance(value, float) else str(value))
            values.append(rendered.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>"))
        lines.append("| " + " | ".join(values) + " |")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _definition(kind: str, parameters: dict[str, Any]) -> dict[str, Any]:
    vectorizer_parameters = _vectorizer_parameters(
        ngram=parameters["ngram"] if kind == "vocabulary" else 1,
        min_df=parameters["min_df"], english_stopwords=parameters["english_stopwords"],
    )
    if kind == "vocabulary":
        return {"analysis_identifier": VOCABULARY_IDENTIFIER,
                "metadata_schema_version": VOCABULARY_METADATA_SCHEMA_VERSION,
                "json_schema_version": VOCABULARY_JSON_SCHEMA_VERSION,
                "csv_schema_version": VOCABULARY_CSV_SCHEMA_VERSION,
                "markdown_schema_version": VOCABULARY_MARKDOWN_SCHEMA_VERSION,
                "vectorizer_parameters": vectorizer_parameters,
                "ranking_identifier": VOCABULARY_RANKING,
                "output_ordering_identifier": VOCABULARY_OUTPUT_ORDERING,
                "sklearn_version": sklearn.__version__,
                "vectorizer_class": "sklearn.feature_extraction.text.CountVectorizer"}
    return {"analysis_identifier": TOPICS_IDENTIFIER,
            "metadata_schema_version": TOPICS_METADATA_SCHEMA_VERSION,
            "json_schema_version": TOPICS_JSON_SCHEMA_VERSION,
            "csv_schema_version": TOPICS_CSV_SCHEMA_VERSION,
            "markdown_schema_version": TOPICS_MARKDOWN_SCHEMA_VERSION,
            "vectorizer_parameters": vectorizer_parameters,
            "lda_parameters": {"n_components": parameters["topic_count"], "max_iter": parameters["max_iter"],
                               "learning_method": "batch", "random_state": parameters["random_seed"]},
            "topic_ordering_identifier": TOPIC_ORDERING,
            "term_ranking_identifier": TOPIC_TERM_RANKING,
            "representative_card_ranking_identifier": REPRESENTATIVE_RANKING,
            "output_ordering_identifier": TOPICS_OUTPUT_ORDERING,
            "current_date_cutoff": parameters["current_date_cutoff"],
            "sklearn_version": sklearn.__version__,
            "vectorizer_class": "sklearn.feature_extraction.text.CountVectorizer",
            "lda_class": "sklearn.decomposition.LatentDirichletAllocation"}


def _key(source: Source, kind: str, parameters: dict[str, Any]) -> str:
    payload = {"analysis_kind": kind, "source_preprocessing_checksum": source.metadata["output_sha256"],
               "definition": _definition(kind, parameters), "parameters": parameters}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _metadata_path(output: Path, kind: str, key: str) -> Path:
    return output / f"{kind}-{key[:KEY_PREFIX_LENGTH]}.metadata.json"


def _data_path(output: Path, kind: str, key: str, checksum: str, format_name: str) -> Path:
    return output / f"{kind}-{key[:KEY_PREFIX_LENGTH]}-{checksum[:CONTENT_PREFIX_LENGTH]}.{OUTPUT_SUFFIXES[format_name]}"


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle: return json.load(handle)


def _expected(source: Source, kind: str, key: str, parameters: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    tokenizer = CountVectorizer(token_pattern=TOKEN_PATTERN).build_tokenizer()
    non_tokenizable_card_ids = [record["card_id"] for record in source.records
                                if record["text_normalized"].strip() and not tokenizer(record["text_normalized"])]
    return {"completed": True, "analysis_cache_key": key, "analysis_kind": kind,
            "source_preprocessing_metadata_file": source.metadata_path.name, "source_preprocessing_data_file": source.data_path.name,
            "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"], "source_preprocessing_checksum": source.metadata["output_sha256"],
            "source_record_count": len(source.records), "non_tokenizable_document_count": len(non_tokenizable_card_ids),
            "non_tokenizable_card_ids": non_tokenizable_card_ids,
            "parameters": parameters, "output_formats": list(OUTPUT_FORMAT_ORDER),
            "result_document_count": result["document_count"], **_definition(kind, parameters)}


def _valid(output: Path, kind: str, key: str, source: Source, parameters: dict[str, Any], result: dict[str, Any]) -> bool:
    try:
        metadata = _read_json(_metadata_path(output, kind, key))
        if not isinstance(metadata, dict) or any(metadata.get(field) != value for field, value in _expected(source, kind, key, parameters, result).items()): return False
        for name in OUTPUT_FORMAT_ORDER:
            checksum, size, filename = metadata.get(f"{name}_output_checksum"), metadata.get(f"{name}_output_file_size"), metadata.get(f"{name}_output_file")
            if not isinstance(checksum, str) or len(checksum) != 64 or type(size) is not int or size < 0 or filename != _data_path(output, kind, key, checksum, name).name: return False
            path = _safe_child(output, filename)
            if path is None or not path.is_file(): return False
            raw = path.read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != checksum or b"\r" in raw or not raw.endswith(b"\n"): return False
        return True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError): return False


def _run(input_metadata: Path, output: Path, *, kind: str, parameters: dict[str, Any], force: bool, writer: AtomicWriter, cutoff: date | None = None) -> dict[str, Any]:
    source = load_source(input_metadata)
    if kind == "vocabulary": result = build_vocabulary(source, **parameters)
    else:
        model_parameters = {key: value for key, value in parameters.items() if key != "current_date_cutoff"}
        result = build_topics(source, **model_parameters, cutoff=cutoff)
    key = _key(source, kind, parameters); metadata_path = _metadata_path(output, kind, key); hit = _valid(output, kind, key, source, parameters, result)
    if hit and not force:
        metadata = _read_json(metadata_path)
        return {"status": "cache_hit", "cache_hit": True, "analysis_cache_key": key, "result": result, "source": source, "output_metadata_path": metadata_path, "output_paths": {name: output / metadata[f"{name}_output_file"] for name in OUTPUT_FORMAT_ORDER}}
    contents = {"json": _serialize_json(result), "csv": _serialize_csv(result, kind), "markdown": _serialize_markdown(result, kind)}
    paths = {name: _data_path(output, kind, key, hashlib.sha256(contents[name]).hexdigest(), name) for name in OUTPUT_FORMAT_ORDER}; created: list[Path] = []
    try:
        output.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_FORMAT_ORDER:
            if paths[name].exists():
                if not paths[name].is_file() or paths[name].read_bytes() != contents[name]: raise OSError("同名generationが期待する内容と一致しません")
            else: writer(paths[name], contents[name]); created.append(paths[name])
        metadata = _expected(source, kind, key, parameters, result)
        for name in OUTPUT_FORMAT_ORDER:
            metadata[f"{name}_output_file"] = paths[name].name; metadata[f"{name}_output_checksum"] = hashlib.sha256(contents[name]).hexdigest(); metadata[f"{name}_output_file_size"] = len(contents[name])
        writer(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode())
    except OSError as exc:
        for path in created:
            try: path.unlink(missing_ok=True)
            except OSError: pass
        raise VocabularyError("分析出力の保存に失敗しました。既存出力は変更していません") from exc
    return {"status": "analyzed", "cache_hit": hit, "analysis_cache_key": key, "result": result, "source": source, "output_metadata_path": metadata_path, "output_paths": paths}


def analyze_vocabulary(input_metadata: Path, output: Path, *, ngram: int = 1, min_df: int = 1, english_stopwords: bool = False, force: bool = False, writer: AtomicWriter = _write_atomic) -> dict[str, Any]:
    return _run(input_metadata, output, kind="vocabulary", parameters={"ngram": ngram, "min_df": min_df, "english_stopwords": english_stopwords}, force=force, writer=writer)


def analyze_topics(input_metadata: Path, output: Path, *, topic_count: int = 5, top_terms: int = 10, representative_cards: int = 10, random_seed: int = 0, max_iter: int = 10, min_df: int = 1, english_stopwords: bool = False, force: bool = False, today: date | None = None, writer: AtomicWriter = _write_atomic) -> dict[str, Any]:
    cutoff = today or datetime.now(timezone.utc).date()
    parameters = {"topic_count": topic_count, "top_terms": top_terms, "representative_cards": representative_cards, "random_seed": random_seed, "max_iter": max_iter, "min_df": min_df, "english_stopwords": english_stopwords, "current_date_cutoff": cutoff.isoformat()}
    result = _run(input_metadata, output, kind="topics", parameters=parameters, force=force, writer=writer, cutoff=cutoff)
    return result
