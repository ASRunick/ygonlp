import hashlib
import json
import sys
import types
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import ygonlp.cli as cli
from ygonlp.preprocess import preprocess
from ygonlp.semantic import SemanticError, embed_effect_text, normalize_query, search_semantic
from ygonlp.semantic_backend import DEFAULT_SPEC
import ygonlp.semantic_backend as backend


SPEC = replace(DEFAULT_SPEC, provider="fake", model_id="test/fake", package_version="1",
               model_revision=None, dimension=2)


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        vectors = {"draw a card": [1, 0], "summon a monster": [0, 1],
                   "return a monster": [0, 1], "special summon": [0, 1]}
        return np.array([vectors[text] for text in texts], dtype=np.float32)


class Factory:
    def __init__(self):
        self.created = []

    def __call__(self):
        value = FakeEmbedder()
        self.created.append(value)
        return value


def card(card_id, name, text):
    return {"id": card_id, "name": name, "type": "Effect Monster", "frameType": "effect",
            "race": "Warrior", "archetype": None, "desc": text,
            "misc_info": [{"has_effect": 1, "tcg_date": "2020-01-01"}]}


def source(tmp_path):
    records = [card(1, "Draw", "draw a card"), card(2, "Summon", "summon a monster"),
               card(3, "Return", "return a monster"), card(4, "Empty", "")]
    raw = json.dumps({"data": records}).encode()
    path = tmp_path / "raw.json"
    path.write_bytes(raw)
    metadata = tmp_path / "raw.metadata.json"
    metadata.write_text(json.dumps({"schema_version": "1", "completed": True,
                                    "cache_key": "raw-key", "data_file": path.name,
                                    "data_sha256": hashlib.sha256(raw).hexdigest(),
                                    "record_count": len(records)}), encoding="utf-8")
    return preprocess(metadata, tmp_path / "preprocessed")["output_metadata_path"]


def corpus(tmp_path, factory):
    input_metadata = source(tmp_path)
    result = embed_effect_text(input_metadata, tmp_path / "embeddings", spec=SPEC,
                               embedder_factory=factory)
    return input_metadata, result


def test_corpus_schema_counts_checksum_and_pre_model_cache_hit(tmp_path):
    factory = Factory()
    input_metadata, first = corpus(tmp_path, factory)
    saved = json.loads(first["metadata_path"].read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1 and saved["completed"] is True
    assert (saved["eligible_count"], saved["embedded_count"], saved["empty_text_count"]) == (3, 3, 1)
    assert saved["model"]["model_revision"] is None
    assert saved["source_preprocessing_metadata_sha256"] == hashlib.sha256(input_metadata.read_bytes()).hexdigest()
    assert saved["source_preprocessing_data_sha256"]
    assert saved["data_sha256"] == hashlib.sha256(first["data_path"].read_bytes()).hexdigest()
    assert saved["data_size"] == first["data_path"].stat().st_size
    assert factory.created[0].calls == [["draw a card", "summon a monster", "return a monster"]]
    assert embed_effect_text(input_metadata, tmp_path / "embeddings", spec=SPEC,
                             embedder_factory=lambda: pytest.fail("model constructed on cache hit"))["status"] == "cache_hit"


def test_corpus_invalidation_force_and_corruption(tmp_path):
    factory = Factory()
    input_metadata, first = corpus(tmp_path, factory)
    assert embed_effect_text(input_metadata, tmp_path / "embeddings", force=True, spec=SPEC,
                             embedder_factory=factory)["status"] == "embedded"
    assert len(factory.created) == 2
    changed = replace(SPEC, package_version="2")
    second = embed_effect_text(input_metadata, tmp_path / "embeddings", spec=changed,
                               embedder_factory=factory)
    assert second["metadata_path"] != first["metadata_path"]
    metadata = json.loads(input_metadata.read_text(encoding="utf-8"))
    metadata["note"] = "source metadata changed"
    input_metadata.write_text(json.dumps(metadata), encoding="utf-8")
    third = embed_effect_text(input_metadata, tmp_path / "embeddings", spec=SPEC,
                              embedder_factory=factory)
    assert third["metadata_path"] != first["metadata_path"]
    first["data_path"].write_bytes(b"tampered")
    with pytest.raises(SemanticError, match="checksum"):
        search_semantic(first["metadata_path"], tmp_path / "results", card_id=1, spec=SPEC)
    with pytest.raises(SemanticError, match="保存"):
        embed_effect_text(input_metadata, tmp_path / "embeddings", spec=changed,
                          embedder_factory=factory, force=True,
                          writer=lambda path, content: (_ for _ in ()).throw(OSError("save failed")))


def test_card_query_raw_ties_excludes_self_and_json_provenance(tmp_path):
    factory = Factory()
    _, embedded = corpus(tmp_path, factory)
    result = search_semantic(embedded["metadata_path"], tmp_path / "results",
                             card_id=2, top_n=3, spec=SPEC,
                             embedder_factory=lambda: pytest.fail("card query constructed model"))
    saved = json.loads(result["data_path"].read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert saved["query"] == {"kind": "card_id", "value": 2}
    assert [row["card_id"] for row in saved["matches"]] == [3, 1]
    assert saved["matches"][0]["score"] == 1.0
    assert saved["ranking_identifier"] == "cosine_raw_desc_card_id_asc_v1"
    assert saved["model"] == SPEC.metadata()
    assert saved["source_preprocessing_metadata_sha256"]
    assert saved["result_cache_key"]
    meta = json.loads(result["metadata_path"].read_text(encoding="utf-8"))
    assert meta["model"] == SPEC.metadata() and meta["query_embedding_data_sha256"] is None
    assert meta["data_size"] == result["data_path"].stat().st_size
    assert search_semantic(embedded["metadata_path"], tmp_path / "results", card_id=2,
                           top_n=3, spec=SPEC)["status"] == "cache_hit"
    with pytest.raises(SemanticError, match="card_id"):
        search_semantic(embedded["metadata_path"], tmp_path / "results", card_id=4, spec=SPEC)


def test_natural_query_cache_reuse_exact_provenance_and_offline(tmp_path):
    factory = Factory()
    _, embedded = corpus(tmp_path, factory)
    output = tmp_path / "results"
    first = search_semantic(embedded["metadata_path"], output, query="  special\n summon  ",
                            spec=SPEC, embedder_factory=factory)
    assert [item["card_id"] for item in first["result"]["matches"]][:2] == [2, 3]
    assert first["result"]["query"] == {"kind": "text", "value": "  special\n summon  ",
                                          "normalized_value": "special summon"}
    assert len(factory.created) == 2
    second = search_semantic(embedded["metadata_path"], output, query="special summon",
                             offline=True, spec=SPEC,
                             embedder_factory=lambda: pytest.fail("offline constructed model"))
    assert second["result"]["query"]["value"] == "special summon"
    assert len(list((output / "query-embeddings").glob("*.metadata.json"))) == 1
    assert json.loads(second["metadata_path"].read_text(encoding="utf-8"))["query_embedding_data_sha256"]
    assert search_semantic(embedded["metadata_path"], output, query="special summon",
                           force=True, spec=SPEC, embedder_factory=factory)["status"] == "searched"
    assert len(factory.created) == 3
    assert search_semantic(embedded["metadata_path"], output, query="special summon",
                           force=True, offline=True, spec=SPEC,
                           embedder_factory=lambda: pytest.fail("offline constructed model"))["status"] == "searched"
    with pytest.raises(SemanticError, match="offline query embedding cache miss"):
        search_semantic(embedded["metadata_path"], output, query="different query", offline=True,
                        spec=SPEC, embedder_factory=lambda: pytest.fail("offline constructed model"))
    assert normalize_query(" A\t B ") == "A B"
    for query in ("", "  \n "):
        with pytest.raises(SemanticError, match="空"):
            search_semantic(embedded["metadata_path"], output, query=query, spec=SPEC)


def test_query_cache_tamper_and_model_mismatch_are_rejected_offline(tmp_path):
    factory = Factory()
    _, embedded = corpus(tmp_path, factory)
    output = tmp_path / "results"
    search_semantic(embedded["metadata_path"], output, query="special summon",
                    spec=SPEC, embedder_factory=factory)
    query_meta = next((output / "query-embeddings").glob("*.metadata.json"))
    data = output / "query-embeddings" / json.loads(query_meta.read_text(encoding="utf-8"))["data_file"]
    data.write_bytes(b"bad")
    with pytest.raises(SemanticError, match="offline query embedding cache miss"):
        search_semantic(embedded["metadata_path"], output, query="special summon", offline=True,
                        spec=SPEC, embedder_factory=lambda: pytest.fail("offline constructed model"))
    with pytest.raises(SemanticError, match="model"):
        search_semantic(embedded["metadata_path"], output, card_id=1,
                        spec=replace(SPEC, model_id="another/model"))


def test_atomic_failure_preserves_existing_artifacts(tmp_path):
    factory = Factory()
    input_metadata, embedded = corpus(tmp_path, factory)
    old_meta = embedded["metadata_path"].read_bytes()
    old_data = embedded["data_path"].read_bytes()

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"):
            raise OSError("metadata failure")
        from ygonlp.artifacts import write_bytes_atomic
        write_bytes_atomic(path, content)

    with pytest.raises(SemanticError, match="保存"):
        embed_effect_text(input_metadata, tmp_path / "embeddings", spec=SPEC, force=True,
                          embedder_factory=factory, writer=fail_metadata)
    assert embedded["metadata_path"].read_bytes() == old_meta
    assert embedded["data_path"].read_bytes() == old_data
    output = tmp_path / "results"
    first = search_semantic(embedded["metadata_path"], output, card_id=1, spec=SPEC)
    first_metadata = first["metadata_path"].read_bytes()
    before = {path.name for path in output.iterdir()}
    with pytest.raises(SemanticError, match="保存"):
        search_semantic(embedded["metadata_path"], output, card_id=1, spec=SPEC,
                        force=True, writer=fail_metadata)
    assert first["metadata_path"].read_bytes() == first_metadata
    with pytest.raises(SemanticError, match="保存"):
        search_semantic(embedded["metadata_path"], output, card_id=2, spec=SPEC,
                        writer=fail_metadata)
    assert first["metadata_path"].exists()
    assert {path.name for path in output.iterdir()} == before


def test_query_metadata_failure_cleans_new_generation(tmp_path):
    factory = Factory()
    _, embedded = corpus(tmp_path, factory)
    output = tmp_path / "results"

    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"):
            raise OSError("metadata failure")
        from ygonlp.artifacts import write_bytes_atomic
        write_bytes_atomic(path, content)

    with pytest.raises(SemanticError, match="保存"):
        search_semantic(embedded["metadata_path"], output, query="special summon", spec=SPEC,
                        embedder_factory=factory, writer=fail_metadata)
    assert list((output / "query-embeddings").iterdir()) == []


def test_cli_help_required_args_and_fake_end_to_end(tmp_path, monkeypatch, capsys):
    for command in ("embed-effect-text", "search-semantic"):
        with pytest.raises(SystemExit) as exc:
            cli.main([command, "--help"])
        assert exc.value.code == 0
    assert "--offline" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        cli.main(["search-semantic", "--embedding-metadata", "missing", "--output", "out"])
    assert exc.value.code == 2
    input_metadata = source(tmp_path)
    factory = Factory()
    monkeypatch.setattr(cli, "embed_effect_text", lambda *a, **kw: embed_effect_text(*a, spec=SPEC, embedder_factory=factory, **kw))
    monkeypatch.setattr(cli, "search_semantic", lambda *a, **kw: search_semantic(*a, spec=SPEC, embedder_factory=factory, **kw))
    assert cli.main(["embed-effect-text", "--input-metadata", str(input_metadata),
                     "--output", str(tmp_path / "embeddings")]) == 0
    embedded = next((tmp_path / "embeddings").glob("*.metadata.json"))
    assert cli.main(["search-semantic", "--embedding-metadata", str(embedded),
                     "--card-id", "2", "--output", str(tmp_path / "results")]) == 0
    assert cli.main(["search-semantic", "--embedding-metadata", str(embedded),
                     "--query", "special summon", "--offline", "--output", str(tmp_path / "results")]) == 1
    assert "cache miss" in capsys.readouterr().err


def test_production_adapter_requests_pinned_snapshot_without_real_model(monkeypatch):
    calls = {}
    hub = types.ModuleType("huggingface_hub")

    def snapshot_download(**kwargs):
        calls.update(kwargs)
        return "local-snapshot"

    hub.snapshot_download = snapshot_download
    model2vec = types.ModuleType("model2vec")

    class Model:
        dim = 256
        normalize = True

        @classmethod
        def from_pretrained(cls, path, normalize):
            assert (path, normalize) == ("local-snapshot", True)
            return cls()

        def encode(self, texts, **kwargs):
            assert kwargs["max_length"] == 512
            assert kwargs["use_multiprocessing"] is False
            return np.ones((len(texts), 256), dtype=np.float32)

    model2vec.StaticModel = Model
    monkeypatch.setitem(sys.modules, "model2vec", model2vec)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(backend, "version", lambda _: "0.9.0")
    adapter = backend.Model2VecEmbedder()
    assert adapter.encode(["test"]).shape == (1, 256)
    assert calls["repo_id"] == backend.MODEL_ID
    assert calls["revision"] == backend.MODEL_REVISION
    assert "model.safetensors" in calls["allow_patterns"]
