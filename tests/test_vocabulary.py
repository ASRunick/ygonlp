import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

import ygonlp.vocabulary as module
from ygonlp.preprocess import preprocess
from ygonlp.vocabulary import VocabularyError, analyze_topics, analyze_vocabulary, build_topics, build_vocabulary


def record(card_id, name, text, **changes):
    value = {"id": card_id, "name": name, "type": "Effect Monster", "frameType": "effect", "race": "Warrior",
             "archetype": None, "desc": text, "misc_info": [{"has_effect": 1, "tcg_date": "2020-01-01"}]}
    value.update(changes)
    return value


def source(tmp_path: Path, records=None):
    records = records or [
        record(1, "Alpha", "the draw card draw card"),
        record(2, "Beta", "draw monster"),
        record(3, "Gamma", "summon monster", type="Spell Card", frameType="spell", misc_info=[{"has_effect": 1, "tcg_date": None}]),
        record(4, "Future", "summon card", misc_info=[{"has_effect": 1, "tcg_date": "2030-01-01"}]),
        record(5, "Empty", ""), record(6, "Tokenless", "}}"),
    ]
    raw = json.dumps({"data": records}).encode(); data = tmp_path / "raw.json"; data.write_bytes(raw)
    metadata = tmp_path / "raw.metadata.json"
    metadata.write_text(json.dumps({"schema_version": "1", "completed": True, "cache_key": "raw-key", "data_file": data.name,
                                    "data_sha256": hashlib.sha256(raw).hexdigest(), "record_count": len(records)}), encoding="utf-8")
    return Path(preprocess(metadata, tmp_path / "preprocessed")["output_metadata_path"])


def loaded(path: Path):
    return module.load_source(path)


def test_unigram_bigram_frequency_document_frequency_stopwords_and_order(tmp_path):
    value = build_vocabulary(loaded(source(tmp_path)), ngram=1)
    terms = {item["term"]: item for item in value["terms"]}
    assert terms["draw"] == {"term": "draw", "corpus_frequency": 3, "document_frequency": 2, "document_frequency_ratio": 0.5}
    assert [item["term"] for item in value["terms"][:2]] == ["card", "draw"]
    bigrams = build_vocabulary(loaded(source(tmp_path)), ngram=2)["terms"]
    assert {item["term"] for item in bigrams} >= {"draw card", "draw monster", "summon monster"}
    assert "the" not in {item["term"] for item in build_vocabulary(loaded(source(tmp_path)), english_stopwords=True)["terms"]}
    assert value["excluded_empty_document_count"] == 1 and value["excluded_tokenless_document_count"] == 1


def test_min_df_and_topic_parameter_validation(tmp_path):
    source_path = source(tmp_path); value = build_vocabulary(loaded(source_path), min_df=2)
    assert [item["term"] for item in value["terms"]] == ["card", "draw", "monster", "summon"]
    for kwargs in ({"min_df": 0}, {"min_df": True}, {"ngram": 3}):
        with pytest.raises(VocabularyError): build_vocabulary(loaded(source_path), **kwargs)
    for kwargs in ({"topic_count": 0}, {"top_terms": 0}, {"representative_cards": 0}, {"max_iter": 0}, {"random_seed": True}):
        with pytest.raises(VocabularyError): build_topics(loaded(source_path), **kwargs)


def test_seeded_topics_representatives_proportions_and_prevalence(tmp_path):
    input_metadata = source(tmp_path); source_data = loaded(input_metadata)
    first = build_topics(source_data, topic_count=2, top_terms=2, representative_cards=2, random_seed=7, max_iter=8, cutoff=date(2021, 1, 1))
    second = build_topics(source_data, topic_count=2, top_terms=2, representative_cards=2, random_seed=7, max_iter=8, cutoff=date(2021, 1, 1))
    assert first == second
    assert [topic["topic_index"] for topic in first["topics"]] == [0, 1]
    assert all(len(topic["representative_cards"]) == 2 for topic in first["topics"])
    for topic in first["topics"]:
        expected = sorted(first["documents"], key=lambda item: (-item["topic_proportions"][topic["topic_index"]], item["card_id"]))[:2]
        assert [card["card_id"] for card in topic["representative_cards"]] == [card["card_id"] for card in expected]
    assert all(sum(document["topic_proportions"]) == pytest.approx(1, abs=1e-6) for document in first["documents"])
    assert [(item["scope"], item["group"]) for item in first["topic_prevalence"]] == [("overall", None), ("card_type", "Effect Monster"), ("card_type", "Spell Card"), ("tcg_year", "2020")]
    assert first["missing_date_count"] == 1 and first["future_date_count"] == 1


def test_output_cache_force_determinism_rollback_and_sklearn_metadata(tmp_path):
    input_metadata = source(tmp_path); output = tmp_path / "output"
    first = analyze_vocabulary(input_metadata, output, ngram=1)
    bytes_before = {name: path.read_bytes() for name, path in first["output_paths"].items()}
    metadata_before = first["output_metadata_path"].read_bytes()
    assert analyze_vocabulary(input_metadata, output, ngram=1)["status"] == "cache_hit"
    forced = analyze_vocabulary(input_metadata, output, ngram=1, force=True)
    assert bytes_before == {name: path.read_bytes() for name, path in forced["output_paths"].items()}
    assert metadata_before == forced["output_metadata_path"].read_bytes()
    assert json.loads(metadata_before)["vectorizer_class"] == "sklearn.feature_extraction.text.CountVectorizer"

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"): raise OSError("fail")
        module._write_atomic(path, content)
    with pytest.raises(VocabularyError): analyze_vocabulary(input_metadata, output, ngram=2, writer=fail_metadata)
    assert first["output_metadata_path"].read_bytes() == metadata_before

    topics = analyze_topics(input_metadata, output, topic_count=2, top_terms=2, representative_cards=2, random_seed=3, max_iter=3, today=date(2021, 1, 1))
    topic_metadata = json.loads(topics["output_metadata_path"].read_text(encoding="utf-8"))
    assert topic_metadata["lda_class"] == "sklearn.decomposition.LatentDirichletAllocation"
    assert topic_metadata["sklearn_version"] == module.sklearn.__version__


def test_metadata_reports_non_tokenizable_documents_for_vocabulary_and_topics(tmp_path):
    input_metadata = source(tmp_path)
    vocabulary = analyze_vocabulary(input_metadata, tmp_path / "vocabulary-output")
    topics = analyze_topics(input_metadata, tmp_path / "topics-output", topic_count=2, top_terms=2,
                            representative_cards=2, random_seed=3, max_iter=3, today=date(2021, 1, 1))

    for analysis in (vocabulary, topics):
        metadata = json.loads(analysis["output_metadata_path"].read_text(encoding="utf-8"))
        assert metadata["non_tokenizable_document_count"] == 1
        assert metadata["non_tokenizable_card_ids"] == [6]


def test_document_filtering_keeps_stopword_and_bigram_behavior(tmp_path):
    input_metadata = source(tmp_path, [
        record(1, "Pair", "alpha beta"), record(2, "Stopword", "the"),
        record(3, "Single", "solo"), record(4, "Symbol", "}}"),
    ])
    source_data = loaded(input_metadata)

    stopword_vocabulary = build_vocabulary(source_data, english_stopwords=True)
    stopword_topics = build_topics(source_data, topic_count=2, top_terms=1, representative_cards=1,
                                   max_iter=2, english_stopwords=True, cutoff=date(2021, 1, 1))
    bigrams = build_vocabulary(source_data, ngram=2)

    assert stopword_vocabulary["document_count"] == 2
    assert stopword_topics["document_count"] == 2
    assert bigrams["document_count"] == 1


def test_topics_cache_force_serialization_rollback_and_metadata_definition(tmp_path):
    input_metadata = source(tmp_path); output = tmp_path / "topics-output"
    kwargs = {"topic_count": 2, "top_terms": 2, "representative_cards": 2, "random_seed": 9,
              "max_iter": 4, "today": date(2021, 1, 1)}
    first = analyze_topics(input_metadata, output, **kwargs)
    output_bytes = {name: path.read_bytes() for name, path in first["output_paths"].items()}
    metadata_bytes = first["output_metadata_path"].read_bytes()
    assert analyze_topics(input_metadata, output, **kwargs)["status"] == "cache_hit"
    forced = analyze_topics(input_metadata, output, force=True, **kwargs)
    assert output_bytes == {name: path.read_bytes() for name, path in forced["output_paths"].items()}
    assert metadata_bytes == forced["output_metadata_path"].read_bytes()
    metadata = json.loads(metadata_bytes)
    for field in ("vectorizer_parameters", "lda_parameters", "topic_ordering_identifier",
                  "term_ranking_identifier", "representative_card_ranking_identifier",
                  "output_ordering_identifier", "analysis_identifier", "current_date_cutoff"):
        assert field in metadata
    assert metadata["vectorizer_parameters"] == {"lowercase": True, "token_pattern": module.TOKEN_PATTERN,
                                                   "ngram_range": [1, 1], "min_df": 1, "stop_words": None, "dtype": "int64"}
    assert metadata["lda_parameters"] == {"n_components": 2, "max_iter": 4, "learning_method": "batch", "random_state": 9}

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"):
            raise OSError("fail")
        module._write_atomic(path, content)

    with pytest.raises(VocabularyError):
        analyze_topics(input_metadata, output, writer=fail_metadata, **{**kwargs, "top_terms": 1})
    assert first["output_metadata_path"].read_bytes() == metadata_bytes
