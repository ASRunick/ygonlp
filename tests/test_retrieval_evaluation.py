import hashlib
import json
import math

import numpy as np
import pytest

from test_semantic import SPEC, Factory, corpus
from ygonlp.retrieval_evaluation import (
    evaluate_retrieval, extract_explicit_metadata, load_cached_corpus,
    load_cached_queries, normalize_draw_query, parse_fixture_effect, ranking_metrics,
    reciprocal_rank_fusion, understand_fixture_query,
)
from ygonlp.semantic import SemanticError, _key, search_semantic


def test_metrics_against_hand_calculation_and_undefined_judgments():
    result = ranking_metrics([30, 20, 10], {10: 3, 20: 1}, 2)
    assert result["recall_at_k"] == 0.5
    assert result["rr_at_k"] == 0.5
    assert result["ndcg_at_k"] == pytest.approx((1 / math.log2(3)) / (7 + 1 / math.log2(3)))
    assert ranking_metrics([20, 10], {10: 1, 20: 1}, 10)["ndcg_at_k"] == 1
    assert ranking_metrics([], {10: 1}, 10) == {"recall_at_k": 0, "rr_at_k": 0, "ndcg_at_k": 0}
    assert ranking_metrics([10], {}, 10) is None
    for ranking, relevance, k in (([10, 10], {10: 1}, 2), ([10], {10: True}, 1), ([10], {10: 4}, 1), ([10], {}, 0)):
        with pytest.raises(ValueError):
            ranking_metrics(ranking, relevance, k)


def test_rrf_uses_ranks_deterministic_ties_and_missing_lists():
    assert reciprocal_rank_fusion([[9, 2], [2, 9]]) == [2, 9]
    assert reciprocal_rank_fusion([[9], []]) == [9]
    assert reciprocal_rank_fusion([[], []]) == []
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[1, 1]])
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[1]], 0)


def test_narrow_numeric_grammar_separates_fixture_cost_and_effect():
    assert understand_fixture_query("Discard two cards to draw three cards.") == {
        "status": "supported_fixture_grammar", "action": "draw", "count": 3,
        "cost": {"action": "discard", "count": 2}, "target": None, "zone": None, "timing_condition": None}
    assert understand_fixture_query("draw 2 cards")["count"] == 2
    assert parse_fixture_effect("Discard 2 cards; draw 3 cards.") == understand_fixture_query("discard two cards to draw three cards")
    assert parse_fixture_effect("Draw 2 cards.")["count"] == 2
    for text in ("Discard 2 cards, then draw 3 cards.", "Draw 2 cards, then discard 1 card.",
                 "You can discard 2 cards; draw 3 cards.", "If this card is destroyed: draw 2 cards."):
        assert parse_fixture_effect(text)["status"] == "unsupported_or_ambiguous"
    assert normalize_draw_query(" Draw\t two cards. ") == "draw 2 cards"
    for query in ("draw until you have 2 cards", "draw 2 or 3 cards", "discard 2 cards to draw 3 cards",
                  "a spell that draws two cards", "draw two cards and discard one", "draw 20 cards"):
        assert normalize_draw_query(query) == query
    for query in ("draw until you have 2 cards", "draw 2 or 3 cards", "draw cards equal to the number discarded",
                  "if destroyed draw 2 cards", "draw two cards and discard one", "draw 0 cards"):
        assert understand_fixture_query(query)["status"] == "unsupported_or_ambiguous"


def test_metadata_prototype_preserves_effect_prose_and_ambiguous_constraints():
    options = {"card_types": {"Spell Card", "Effect Monster"}, "races": {"Spellcaster", "Warrior"}}
    assert extract_explicit_metadata("card_type=Effect Monster; race=Spellcaster; summon from GY", **options) == {
        "status": "explicit_metadata", "filters": {"card_type": "Effect Monster", "race": "Spellcaster"}, "query": "summon from GY"}
    for query in ("special summon a Spellcaster monster", "destroy a spell card", "not a Warrior"):
        assert extract_explicit_metadata(query, **options) == {"status": "unstructured", "query": query, "filters": {}}
    for query in ("race=Unknown; draw 2 cards", "race=Warrior; race=Spellcaster; draw 2 cards",
                  "level=4; draw 2 cards", "card_type=Spell Card;", "race=Warrior"):
        result = extract_explicit_metadata(query, **options)
        assert result["status"] == "unsupported_or_ambiguous" and result["filters"] == {} and result["query"] == query


def test_comparison_exposes_numeric_baseline_failure_without_claiming_model_quality():
    cards = [
        {"card_id": 1, "card_type": "Spell Card", "race": None, "text_normalized": "Draw 1 card."},
        {"card_id": 2, "card_type": "Spell Card", "race": None, "text_normalized": "Draw 2 cards."},
        {"card_id": 3, "card_type": "Effect Monster", "race": "Spellcaster", "text_normalized": "Draw 2 cards."},
        {"card_id": 4, "card_type": "Spell Card", "race": None, "text_normalized": "Discard 2 cards; draw 3 cards."},
    ]
    matrix = np.array([[1, 0], [1, 0], [1, 0], [1, 0]])  # mock deliberately loses count
    cases = [{"id": "numeric-spell", "query": "draw two cards", "filters": {"card_type": "Spell Card"},
              "judgment": {"kind": "authored_fixture", "source": "Exhaustive authored fixture properties; mock vectors.", "relevance": {"2": 1}}},
             {"id": "unknown", "query": "a useful combo", "judgment": {"kind": "human_review_required", "source": "Needs human meaning judgments."}}]
    result = evaluate_retrieval(cards, matrix, cases, np.array([[1, 0], [1, 0]]), k=1)
    query = result["queries"][0]
    assert query["rankings"]["semantic"] == [1]
    assert query["rankings"]["normalized_lexical"] == [2]
    assert query["metrics"]["semantic"]["recall_at_k"] == 0
    assert query["metrics"]["normalized_lexical"]["recall_at_k"] == 1
    assert result["queries"][1]["judgment_status"] == "human_review_required"
    assert all(value is None for value in result["queries"][1]["metrics"].values())
    assert result["groups"]["authored_fixture"]["judged_query_count"] == 1
    assert result["groups"]["surface_proxy"]["judged_query_count"] == 0
    assert evaluate_retrieval(cards[::-1], matrix, cases, np.array([[1, 0], [1, 0]]), k=1)["queries"] == result["queries"]


def test_surface_proxy_filters_before_topk_and_excludes_undefined_cases():
    cards = [{"card_id": 1, "text_normalized": "Draw 2 cards.", "race": "Warrior"},
             {"card_id": 2, "text_normalized": "Draw 2 cards.", "race": "Spellcaster"}]
    cases = [{"id": "race", "query": "draw 2 cards", "filters": {"race": "Spellcaster"},
              "judgment": {"kind": "surface_proxy", "pattern": "draw 2 cards", "source": "Literal phrase and metadata only."}},
             {"id": "zero", "query": "draw 2 cards", "filters": {"race": "Dragon"},
              "judgment": {"kind": "surface_proxy", "pattern": "draw 2 cards", "source": "Literal phrase and metadata only."}}]
    result = evaluate_retrieval(cards, np.array([[1, 0], [1, 0]]), cases, np.array([[1, 0], [1, 0]]), k=1)
    assert result["queries"][0]["rankings"]["metadata_semantic"] == [2]
    assert result["queries"][1]["rankings"]["metadata_semantic"] == []
    assert result["queries"][1]["judgment_status"] == "no_positive_judgments"
    assert result["groups"]["surface_proxy"]["judged_query_count"] == 1


def test_verified_caches_align_source_and_never_load_model(tmp_path):
    original, embedded = corpus(tmp_path, Factory())
    cards, matrix, provenance = load_cached_corpus(original, embedded["metadata_path"], SPEC)
    assert len(cards) == len(matrix) == 3
    assert provenance["legacy_race_from_verified_source"] is False
    output = tmp_path / "query-results"
    search_semantic(embedded["metadata_path"], output, query="special summon", spec=SPEC, embedder_factory=Factory())
    cases = [{"id": "summon", "query": "special\t summon", "judgment": {"kind": "human_review_required", "source": "test"}}]
    vectors, query_provenance = load_cached_queries(cases, [tmp_path / "absent", output / "query-embeddings"], SPEC)
    assert vectors.tolist() == [[0, 1]] and query_provenance[0]["query_data_sha256"]
    with pytest.raises(SemanticError, match="cache miss"):
        load_cached_queries(cases, [tmp_path / "absent"], SPEC)
    saved = json.loads(embedded["metadata_path"].read_text(encoding="utf-8"))
    saved["cards"][0]["card_type"] = "Spell Card"
    embedded["metadata_path"].write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(SemanticError, match="manifest"):
        load_cached_corpus(original, embedded["metadata_path"], SPEC)


def test_legacy_research_adapter_requires_matching_source_and_recovers_race(tmp_path):
    original, embedded = corpus(tmp_path, Factory())
    saved = json.loads(embedded["metadata_path"].read_text(encoding="utf-8"))
    saved["schema_version"] = 1
    payload = {key: saved[key] for key in ("schema_version", "source_preprocessing_metadata_sha256",
               "source_preprocessing_data_sha256", "source_preprocessing_cache_key", "model", "text_field", "selection")}
    saved["corpus_cache_key"] = _key(payload)
    for card in saved["cards"]:
        card.pop("race")
    raw = embedded["data_path"].read_bytes()
    saved["data_file"] = f"effect-embeddings-{saved['corpus_cache_key'][:16]}-{hashlib.sha256(raw).hexdigest()[:16]}.npy"
    legacy = embedded["metadata_path"].parent / f"effect-embeddings-{saved['corpus_cache_key'][:16]}.metadata.json"
    legacy.write_text(json.dumps(saved), encoding="utf-8")
    (legacy.parent / saved["data_file"]).write_bytes(raw)
    cards, _, provenance = load_cached_corpus(original, legacy, SPEC)
    assert [card["race"] for card in cards] == ["Warrior"] * 3
    assert provenance["legacy_race_from_verified_source"] is True
    with pytest.raises(SemanticError, match="schema"):
        search_semantic(legacy, tmp_path / "search", card_id=1, spec=SPEC)
    original.write_text(original.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SemanticError, match="一致"):
        load_cached_corpus(original, legacy, SPEC)


@pytest.mark.parametrize("vectors", [np.array([[0, 0]]), np.array([[np.nan, 1]]), np.array([[1, 0, 0]])])
def test_invalid_vectors_are_not_evidence(vectors):
    cards = [{"card_id": 1, "text_normalized": "draw 2 cards"}]
    cases = [{"id": "q", "query": "draw 2 cards", "judgment": {"kind": "authored_fixture", "source": "fixture", "relevance": {"1": 1}}}]
    with pytest.raises(ValueError, match="vectors"):
        evaluate_retrieval(cards, np.array([[1, 0]]), cases, vectors)


def test_research_cli_invalid_cases_fail_without_output(tmp_path, capsys):
    from scripts.evaluate_retrieval import main
    cases = tmp_path / "cases.json"
    cases.write_text('[{"id": "q", "query": 42}]', encoding="utf-8")
    output = tmp_path / "result.json"
    assert main(["--cases", str(cases), "--preprocessing-metadata", "missing", "--embedding-metadata", "missing",
                 "--query-cache", "missing", "--output", str(output)]) == 1
    assert not output.exists()
    assert "offline evaluation failed" in capsys.readouterr().err
