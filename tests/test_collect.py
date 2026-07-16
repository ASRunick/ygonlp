import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

import ygonlp.collect as module
from ygonlp.collect import RequestSpec, cache_key, collect, metadata_path, valid_cache


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


def response(status=200, payload=None):
    request = httpx.Request("GET", "https://example.test")
    if payload is None:
        return httpx.Response(status, request=request, content=b"not-json")
    return httpx.Response(status, request=request, json=payload, headers={"content-type": "application/json"})


def good_payload(identifier=1):
    return {"data": [{"id": identifier}]}


def save_good_cache(tmp_path: Path, identifier=1):
    return collect(tmp_path, client=FakeClient([response(payload=good_payload(identifier))]), sleep=lambda _: None)


def read_metadata(tmp_path: Path):
    key = cache_key(RequestSpec())
    return json.loads(metadata_path(tmp_path, key).read_text(encoding="utf-8"))


def test_cache_key_same_conditions_same_key():
    assert cache_key(RequestSpec()) == cache_key(RequestSpec())


def test_cache_key_query_order_independent():
    assert cache_key(RequestSpec(params={"a": "1", "b": "2"})) == cache_key(RequestSpec(params={"b": "2", "a": "1"}))


def test_cache_key_endpoint_changes():
    assert cache_key(RequestSpec()) != cache_key(RequestSpec(endpoint="https://example.test/cards"))


def test_cache_key_parameter_value_changes():
    assert cache_key(RequestSpec()) != cache_key(RequestSpec(params={"misc": "no"}))


def test_cache_key_api_version_changes():
    assert cache_key(RequestSpec()) != cache_key(RequestSpec(api_version="v8"))


def test_cache_key_schema_version_changes(monkeypatch):
    original = cache_key(RequestSpec())
    monkeypatch.setattr(module, "SCHEMA_VERSION", "next")
    assert original != cache_key(RequestSpec())


@pytest.mark.parametrize(
    "mutation",
    [
        "metadata_missing", "data_missing", "metadata_broken", "data_broken", "schema", "key",
        "completed", "data_key", "data_not_list", "data_empty", "checksum", "record_count",
        "absolute", "parent", "outside",
    ],
)
def test_invalid_cache_cases(tmp_path, mutation):
    result = save_good_cache(tmp_path)
    key = result["cache_key"]
    meta_path = Path(result["metadata_path"])
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    data_path = tmp_path / metadata["data_file"]
    if mutation == "metadata_missing":
        meta_path.unlink()
    elif mutation == "data_missing":
        data_path.unlink()
    elif mutation == "metadata_broken":
        meta_path.write_text("{", encoding="utf-8")
    elif mutation == "data_broken":
        data_path.write_text("{", encoding="utf-8")
    elif mutation == "schema":
        metadata["schema_version"] = "old"
    elif mutation == "key":
        metadata["cache_key"] = "wrong"
    elif mutation == "completed":
        metadata["completed"] = False
    elif mutation == "data_key":
        data_path.write_text(json.dumps({"items": []}), encoding="utf-8")
    elif mutation == "data_not_list":
        data_path.write_text(json.dumps({"data": {}}), encoding="utf-8")
    elif mutation == "data_empty":
        data_path.write_text(json.dumps({"data": []}), encoding="utf-8")
    elif mutation == "checksum":
        metadata["data_sha256"] = "0" * 64
    elif mutation == "record_count":
        metadata["record_count"] = 999
    elif mutation == "absolute":
        metadata["data_file"] = str(data_path.resolve())
    elif mutation == "parent":
        metadata["data_file"] = "../outside.json"
    elif mutation == "outside":
        outside = tmp_path.parent / "outside.json"
        outside.write_bytes(data_path.read_bytes())
        metadata["data_file"] = outside.name
    if mutation not in {"metadata_missing", "metadata_broken"}:
        if mutation not in {"data_broken", "data_key", "data_not_list", "data_empty"}:
            meta_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert not valid_cache(tmp_path, key)


def test_normal_cache_is_valid_and_reused(tmp_path):
    first = save_good_cache(tmp_path)
    hit_client = FakeClient([])
    second = collect(tmp_path, client=hit_client)
    assert valid_cache(tmp_path, first["cache_key"])
    assert second["status"] == "cache_hit"
    assert hit_client.calls == []


def test_force_does_not_use_cache_hit(tmp_path):
    save_good_cache(tmp_path)
    client = FakeClient([response(payload=good_payload(2))])
    assert collect(tmp_path, force=True, client=client)["status"] == "fetched"
    assert len(client.calls) == 1


def test_success_saves_generation_metadata_and_checksum(tmp_path):
    result = save_good_cache(tmp_path)
    metadata = read_metadata(tmp_path)
    data = tmp_path / metadata["data_file"]
    assert data.name.startswith("cards-") and data.name.endswith(".json")
    assert metadata["data_file"] == Path(result["data_path"]).name
    assert metadata["data_sha256"] == hashlib.sha256(data.read_bytes()).hexdigest()
    assert metadata["record_count"] == 1
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("payload", [None, [], {"items": []}, {"data": {}}, {"data": []}])
def test_invalid_200_response_does_not_retry(tmp_path, payload):
    client = FakeClient([response(payload=payload)])
    with pytest.raises(RuntimeError):
        collect(tmp_path, client=client, sleep=lambda _: None)
    assert len(client.calls) == 1


@pytest.mark.parametrize("status", [400, 404])
def test_4xx_does_not_retry(tmp_path, status):
    client = FakeClient([response(status=status, payload=good_payload())])
    with pytest.raises(RuntimeError):
        collect(tmp_path, client=client, sleep=lambda _: None)
    assert len(client.calls) == 1


def test_429_does_not_retry_or_sleep(tmp_path):
    client = FakeClient([response(status=429, payload=good_payload())])
    sleeps = []
    with pytest.raises(RuntimeError, match="429"):
        collect(tmp_path, client=client, sleep=sleeps.append)
    assert len(client.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [500, 503])
def test_5xx_retries_at_most_three_times(tmp_path, status):
    client = FakeClient([response(status=status, payload=good_payload())] * 3)
    with pytest.raises(RuntimeError):
        collect(tmp_path, client=client, sleep=lambda _: None, jitter=lambda: 0)
    assert len(client.calls) == 3


@pytest.mark.parametrize("failure", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
def test_network_failures_retry_at_most_three_times(tmp_path, failure):
    client = FakeClient([failure, failure, failure])
    with pytest.raises(RuntimeError):
        collect(tmp_path, client=client, sleep=lambda _: None, jitter=lambda: 0)
    assert len(client.calls) == 3


def test_retry_success_backoff_and_jitter(tmp_path):
    client = FakeClient([response(status=500, payload=good_payload()), response(payload=good_payload())])
    sleeps = []
    result = collect(tmp_path, client=client, sleep=sleeps.append, jitter=lambda: 0.5)
    assert result["attempts"] == 2
    assert sleeps == [1.05]


def test_data_write_failure_keeps_old_cache(tmp_path):
    old = save_good_cache(tmp_path, 1)
    old_metadata = Path(old["metadata_path"]).read_bytes()
    def fail_data(path, content):
        raise OSError("data write failure")
    with pytest.raises(RuntimeError, match="既存キャッシュ"):
        collect(tmp_path, force=True, client=FakeClient([response(payload=good_payload(2))]), writer=fail_data)
    assert Path(old["metadata_path"]).read_bytes() == old_metadata
    assert valid_cache(tmp_path, old["cache_key"])


def test_metadata_write_failure_keeps_old_cache_and_removes_new_data(tmp_path):
    old = save_good_cache(tmp_path, 1)
    old_metadata = Path(old["metadata_path"]).read_bytes()
    def fail_metadata(path, content):
        if path.name.endswith("metadata.json"):
            raise OSError("metadata write failure")
        module._write_bytes_atomic(path, content)
    with pytest.raises(RuntimeError):
        collect(tmp_path, force=True, client=FakeClient([response(payload=good_payload(2))]), writer=fail_metadata)
    assert Path(old["metadata_path"]).read_bytes() == old_metadata
    assert valid_cache(tmp_path, old["cache_key"])
    assert len(list(tmp_path.glob("*.json"))) == 2  # old data + metadata only


def test_metadata_replace_failure_keeps_old_cache(tmp_path, monkeypatch):
    old = save_good_cache(tmp_path, 1)
    old_metadata = Path(old["metadata_path"]).read_bytes()
    original_replace = os.replace
    def fail_metadata_replace(source, destination):
        if Path(destination).name.endswith("metadata.json"):
            raise OSError("metadata replace failure")
        return original_replace(source, destination)
    monkeypatch.setattr(module.os, "replace", fail_metadata_replace)
    with pytest.raises(RuntimeError):
        collect(tmp_path, force=True, client=FakeClient([response(payload=good_payload(2))]))
    assert Path(old["metadata_path"]).read_bytes() == old_metadata
    assert valid_cache(tmp_path, old["cache_key"])
    assert not list(tmp_path.glob("*.tmp"))


def test_force_fetch_failure_keeps_old_cache(tmp_path):
    old = save_good_cache(tmp_path, 1)
    with pytest.raises(RuntimeError):
        collect(tmp_path, force=True, client=FakeClient([response(status=500, payload=good_payload())] * 3), sleep=lambda _: None)
    assert valid_cache(tmp_path, old["cache_key"])


def test_new_metadata_commits_new_generation(tmp_path):
    old = save_good_cache(tmp_path, 1)
    new = collect(tmp_path, force=True, client=FakeClient([response(payload=good_payload(2))]))
    metadata = read_metadata(tmp_path)
    assert metadata["data_file"] == Path(new["data_path"]).name
    assert Path(new["data_path"]).read_text(encoding="utf-8").find('"id": 2') >= 0
    assert Path(old["data_path"]).exists()  # old generation may safely remain unreferenced
    assert valid_cache(tmp_path, new["cache_key"])
