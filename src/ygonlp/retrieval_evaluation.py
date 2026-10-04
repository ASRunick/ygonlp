"""Offline research comparisons; these methods do not change production search.

Surface predicates measure literal retrieval, never gameplay equivalence. Synthetic
vectors are suitable for software tests only, not evidence of model quality.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import sklearn

from .artifacts import read_json
from .measure import load_source
from .semantic import (
    QUERY_NORMALIZATION, SELECTION_IDENTIFIER, SemanticError, _digest, _key,
    _matrix, _read_artifact, normalize_query,
)
from .semantic_backend import DEFAULT_SPEC, EmbeddingSpec
from .similarity import VECTORIZER_PARAMETERS, _vectorizer

METHODS = ("semantic", "lexical", "metadata_semantic", "hybrid_rrf",
           "normalized_lexical", "filtered_hybrid_rrf")
NUMBERS = {word: str(number) for number, word in enumerate(
    ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine"), 1)}
COUNT = r"([1-9]|one|two|three|four|five|six|seven|eight|nine)"


def validate_cases(cases: Any) -> None:
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a nonempty array")
    seen = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"].strip()
                or case["id"] in seen or not isinstance(case.get("query"), str)
                or not isinstance(case.get("judgment"), dict)):
            raise ValueError("each case requires a unique nonblank ID, query string and judgment object")
        normalize_query(case["query"])
        seen.add(case["id"])


def normalize_draw_query(query: str) -> str:
    """Experimental rewrite only for the entire unqualified 'draw N cards' query."""
    value = normalize_query(query)
    match = re.fullmatch(rf"draw {COUNT} cards?\.?", value, re.IGNORECASE)
    if match is None:
        return value
    count = NUMBERS.get(match[1].lower(), match[1])
    return f"draw {count} card" + ("s" if count != "1" else "")


def understand_fixture_query(query: str) -> dict[str, Any]:
    """A deliberately narrow research grammar, with no guessing on other inputs."""
    value = normalize_query(query)
    draw = re.fullmatch(rf"draw {COUNT} cards?\.?", value, re.IGNORECASE)
    cost = re.fullmatch(rf"discard {COUNT} cards? to draw {COUNT} cards?\.?", value, re.IGNORECASE)
    if draw or cost:
        match = cost or draw
        counts = [int(NUMBERS.get(v.lower(), v)) for v in match.groups()]
        return {"status": "supported_fixture_grammar", "action": "draw",
                "count": counts[-1], "cost": {"action": "discard", "count": counts[0]} if cost else None,
                "target": None, "zone": None, "timing_condition": None}
    return {"status": "unsupported_or_ambiguous", "original_query": query}


def parse_fixture_effect(text: str) -> dict[str, Any]:
    """Parse only the two authored effect templates, not arbitrary card PSCT."""
    value = normalize_query(text)
    cost = re.fullmatch(rf"discard {COUNT} cards?; draw {COUNT} cards?\.", value, re.IGNORECASE)
    if cost:
        return understand_fixture_query(f"discard {cost[1]} cards to draw {cost[2]} cards")
    if re.fullmatch(rf"draw {COUNT} cards?\.", value, re.IGNORECASE):
        return understand_fixture_query(value)
    return {"status": "unsupported_or_ambiguous", "original_text": text}


def extract_explicit_metadata(query: str, *, card_types: set[str], races: set[str]) -> dict[str, Any]:
    """Prototype for explicit 'card_type=X; race=Y; text', not natural prose.

    On unknown, repeated or conflicting constraints, preserve the entire query
    and apply no inferred filters. Metadata mentioned in effect prose is untouched.
    """
    remaining = normalize_query(query)
    original = remaining
    filters: dict[str, str] = {}
    while "=" in remaining.split(";", 1)[0]:
        clause, separator, tail = remaining.partition(";")
        field, equals, value = clause.partition("=")
        field, value = field.strip(), value.strip()
        allowed = card_types if field == "card_type" else races if field == "race" else set()
        if not separator or not equals or field in filters or value not in allowed:
            return {"status": "unsupported_or_ambiguous", "query": original, "filters": {}}
        filters[field] = value
        remaining = tail.strip()
    if not remaining:
        return {"status": "unsupported_or_ambiguous", "query": original, "filters": {}}
    return {"status": "explicit_metadata" if filters else "unstructured", "query": remaining, "filters": filters}


def ranking_metrics(ranking: list[int], relevance: dict[int, int], k: int) -> dict[str, float] | None:
    """Recall@k, reciprocal rank@k, nDCG@k with gain 2**grade-1.

    No positive judgments is undefined, not a perfect or zero-quality query.
    Relevance must cover the evaluation universe (omitted IDs are grade zero).
    """
    if type(k) is not int or k <= 0 or len(ranking) != len(set(ranking)):
        raise ValueError("positive k and unique ranking IDs are required")
    if any(type(grade) is not int or not 0 <= grade <= 3 for grade in relevance.values()):
        raise ValueError("relevance grades must be integers in 0..3")
    relevant = {card_id for card_id, grade in relevance.items() if grade > 0}
    if not relevant:
        return None
    selected = ranking[:k]
    rr = next((1 / position for position, card_id in enumerate(selected, 1) if card_id in relevant), 0.0)
    dcg = sum((2 ** relevance.get(card_id, 0) - 1) / math.log2(position + 1)
              for position, card_id in enumerate(selected, 1))
    ideal = sum((2 ** grade - 1) / math.log2(position + 1)
                for position, grade in enumerate(sorted(relevance.values(), reverse=True)[:k], 1))
    return {"recall_at_k": len(relevant.intersection(selected)) / len(relevant),
            "rr_at_k": rr, "ndcg_at_k": dcg / ideal}


def reciprocal_rank_fusion(rankings: list[list[int]], constant: int = 60) -> list[int]:
    """Fuse whole ranked lists, equal weights, 1-based ranks, ID tie breaking."""
    if type(constant) is not int or constant <= 0:
        raise ValueError("RRF constant must be a positive integer")
    scores: dict[int, float] = {}
    for ranking in rankings:
        if len(ranking) != len(set(ranking)):
            raise ValueError("RRF input IDs must be unique")
        for rank, card_id in enumerate(ranking, 1):
            scores[card_id] = scores.get(card_id, 0.0) + 1 / (constant + rank)
    return sorted(scores, key=lambda card_id: (-scores[card_id], card_id))


def load_cached_corpus(preprocessing_metadata: Path, embedding_metadata: Path,
                       spec: EmbeddingSpec = DEFAULT_SPEC) -> tuple[list[dict[str, Any]], np.ndarray, dict[str, Any]]:
    """Verify source identity and row mapping, including research-only schema 1.

    Legacy race is recovered solely from the matching verified preprocessing
    source. Production corpus validation remains unchanged.
    """
    source = load_source(preprocessing_metadata)
    header = read_json(embedding_metadata)
    schema = header.get("schema_version") if isinstance(header, dict) else None
    if type(schema) is not int or schema not in (1, 2):
        raise SemanticError("research corpus schema must be 1 or 2")
    payload = {"schema_version": schema,
               "source_preprocessing_metadata_sha256": _digest(preprocessing_metadata.read_bytes()),
               "source_preprocessing_data_sha256": source.metadata["output_sha256"],
               "source_preprocessing_cache_key": source.metadata["preprocessing_cache_key"],
               "model": spec.metadata(), "text_field": "text_normalized", "selection": SELECTION_IDENTIFIER}
    metadata, raw = _read_artifact(embedding_metadata, "effect-embeddings", "corpus_cache_key", schema, payload)
    if metadata.get("corpus_cache_key") != _key(payload) or metadata.get("data_format") != "npy":
        raise SemanticError("research corpus key or format mismatch")
    saved = metadata.get("cards")
    if not isinstance(saved, list) or metadata.get("embedded_count") != len(saved):
        raise SemanticError("research corpus cards/count mismatch")
    by_id = {card["card_id"]: card for card in source.records}
    cards = []
    fields = ("card_id", "name", "card_type", "tcg_date") + (("race",) if schema == 2 else ())
    for card in saved:
        original = by_id.get(card.get("card_id")) if isinstance(card, dict) else None
        if (original is None or any(field not in card or card[field] != original[field] for field in fields)
                or not original["is_effect_text_target"] or not original["text_normalized"].strip()):
            raise SemanticError("research corpus row does not match verified source")
        cards.append(original)
    ids = [card["card_id"] for card in cards]
    if ids != sorted(set(ids)):
        raise SemanticError("research corpus IDs must be unique and sorted")
    matrix = _matrix(raw, len(cards), spec.dimension)
    provenance = {**payload, "corpus_cache_key": metadata["corpus_cache_key"],
                  "corpus_data_sha256": metadata["data_sha256"],
                  "corpus_metadata_sha256": _digest(embedding_metadata.read_bytes()),
                  "legacy_race_from_verified_source": schema == 1}
    return cards, matrix, provenance


def load_cached_queries(cases: list[dict[str, Any]], directories: list[Path],
                        spec: EmbeddingSpec = DEFAULT_SPEC) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Load query caches only; never construct a model or call a network."""
    validate_cases(cases)
    vectors, provenance = [], []
    for case in cases:
        normalized = normalize_query(case["query"])
        payload = {"schema_version": 1, "model": spec.metadata(),
                   "normalized_query": normalized, "query_normalization": QUERY_NORMALIZATION}
        key = _key(payload)
        hit = None
        for directory in directories:
            path = directory / f"query-embedding-{key[:16]}.metadata.json"
            if not path.exists():
                continue
            metadata, raw = _read_artifact(path, "query-embedding", "query_cache_key", 1, payload)
            if metadata.get("query_cache_key") != key or metadata.get("data_format") != "npy":
                raise SemanticError("research query cache key or format mismatch")
            hit = (_matrix(raw, 1, spec.dimension)[0], metadata)
            break
        if hit is None:
            raise SemanticError(f"offline evaluation query cache miss: {case['id']}")
        vectors.append(hit[0])
        provenance.append({"id": case["id"], "query_cache_key": key,
                           "query_data_sha256": hit[1]["data_sha256"]})
    return np.asarray(vectors, dtype=np.float32), provenance


def evaluate_retrieval(cards: list[dict[str, Any]], matrix: np.ndarray,
                       cases: list[dict[str, Any]], query_vectors: np.ndarray, *,
                       k: int = 10, rrf_constant: int = 60) -> dict[str, Any]:
    """Compare fixed candidates with explicit exhaustive judgments or proxies."""
    validate_cases(cases)
    if type(k) is not int or k <= 0 or type(rrf_constant) is not int or rrf_constant <= 0:
        raise ValueError("k and RRF constant must be positive integers")
    ids = [card["card_id"] for card in cards]
    if not cards or len(ids) != len(set(ids)) or any(type(card_id) is not int for card_id in ids):
        raise ValueError("nonempty corpus and unique integer card IDs are required")
    matrix = np.asarray(matrix, dtype=np.float64)
    query_vectors = np.asarray(query_vectors, dtype=np.float64)
    if (matrix.ndim != 2 or matrix.shape[0] != len(cards) or not matrix.shape[1]
            or query_vectors.shape != (len(cases), matrix.shape[1])
            or not np.isfinite(matrix).all() or not np.isfinite(query_vectors).all()
            or np.any(np.linalg.norm(matrix, axis=1) == 0)
            or np.any(np.linalg.norm(query_vectors, axis=1) == 0)):
        raise ValueError("finite nonzero vectors of matching dimensions are required")
    vectorizer = _vectorizer()
    try:
        lexical = vectorizer.fit_transform([card["text_normalized"] for card in cards])
    except ValueError as exc:
        raise ValueError("evaluation corpus has no lexical tokens") from exc
    unit_matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    def ranked(scores, *, candidates=None, positive=False):
        return [ids[index] for index in sorted(range(len(cards)), key=lambda i: (-float(scores[i]), ids[i]))
                if (candidates is None or ids[index] in candidates) and (not positive or scores[index] > 0)]

    results = []
    for case, query_vector in zip(cases, query_vectors):
        query = normalize_query(case["query"])
        filters = case.get("filters", {})
        if (not isinstance(filters, dict) or set(filters) - {"card_type", "race"}
                or any(not isinstance(value, str) or not value for value in filters.values())):
            raise ValueError("evaluation filters must be exact card_type/race strings")
        candidates = {card["card_id"] for card in cards
                      if all(card.get(field) == value for field, value in filters.items())}
        semantic_scores = unit_matrix @ (query_vector / np.linalg.norm(query_vector))
        lexical_scores = (lexical @ vectorizer.transform([query]).T).toarray().ravel()
        rewritten = normalize_draw_query(query)
        normalized_scores = (lexical @ vectorizer.transform([rewritten]).T).toarray().ravel()
        semantic = ranked(semantic_scores)
        words = ranked(lexical_scores, positive=True)
        filtered_semantic = ranked(semantic_scores, candidates=candidates)
        filtered_words = ranked(lexical_scores, candidates=candidates, positive=True)
        rankings = dict(zip(METHODS, (
            semantic, words, filtered_semantic, reciprocal_rank_fusion([semantic, words], rrf_constant),
            ranked(normalized_scores, positive=True),
            reciprocal_rank_fusion([filtered_semantic, filtered_words], rrf_constant))))
        judgment = case["judgment"]
        kind = judgment["kind"]
        if not isinstance(judgment.get("source"), str) or not judgment["source"].strip():
            raise ValueError("judgment source is required")
        if kind == "surface_proxy":
            pattern = re.compile(judgment["pattern"], re.IGNORECASE)
            relevance = {card["card_id"]: 1 for card in cards if card["card_id"] in candidates
                         and pattern.search(card["text_normalized"])}
        elif kind == "authored_fixture":
            if (not isinstance(judgment.get("relevance"), dict)
                    or any(not isinstance(card_id, str) or not card_id.isdecimal()
                           or str(int(card_id)) != card_id for card_id in judgment["relevance"])):
                raise ValueError("authored judgment IDs must be canonical integer strings")
            relevance = {int(card_id): grade for card_id, grade in judgment["relevance"].items()}
            if set(relevance) - set(ids):
                raise ValueError("judgment IDs must belong to corpus")
        elif kind == "human_review_required":
            relevance = None
        else:
            raise ValueError("unsupported judgment kind")
        metrics = {method: ranking_metrics(ranking, relevance, k) if relevance is not None else None
                   for method, ranking in rankings.items()}
        status = "human_review_required" if relevance is None else "judged" if any(relevance.values()) else "no_positive_judgments"
        results.append({"id": case["id"], "query": case["query"], "normalized_lexical_query": rewritten,
                        "filters": filters, "candidate_count": len(candidates), "judgment": judgment,
                        "judgment_status": status, "positive_judgment_count": sum(v > 0 for v in relevance.values()) if relevance is not None else None,
                        "rankings": {method: ranking[:k] for method, ranking in rankings.items()}, "metrics": metrics})
    groups = {}
    for kind in ("surface_proxy", "authored_fixture"):
        judged = [row for row in results if row["judgment"]["kind"] == kind and row["judgment_status"] == "judged"]
        groups[kind] = {"judged_query_count": len(judged), "macro_metrics": {
            method: {metric: sum(row["metrics"][method][metric] for row in judged) / len(judged)
                     for metric in ("recall_at_k", "rr_at_k", "ndcg_at_k")} if judged else None
            for method in METHODS}}
    return {"schema_version": 1, "k": k, "corpus_count": len(cards), "query_count": len(cases),
            "methods": list(METHODS), "rrf_constant": rrf_constant,
            "ranking": "raw_score_desc_card_id_asc; lexical_positive_only; full_lists_equal_weight_rrf_1_based",
            "metric_definition": "recall@k; RR@k (macro=MRR@k); nDCG@k gain=2^grade-1 discount=log2(rank+1); no positives undefined",
            "sklearn_version": sklearn.__version__, "numpy_version": np.__version__,
            "vectorizer_parameters": VECTORIZER_PARAMETERS, "query_normalization": QUERY_NORMALIZATION,
            "rewrite_identifier": "whole_draw_one_to_nine_cards_v1", "groups": groups, "queries": results}
