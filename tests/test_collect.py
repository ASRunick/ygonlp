import json
import hashlib
from pathlib import Path

import httpx
import pytest

from ygonlp.collect import RequestSpec, cache_key, cache_paths, collect, valid_cache


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def response(status=200, payload=None, headers=None):
    request = httpx.Request("GET", "https://example.test")
    if payload is None:
        return httpx.Response(status, request=request, content=b"not-json")
    return httpx.Response(status, request=request, json=payload, headers=headers or {"content-type": "application/json"})


def test_cache_key_is_order_independent_and_sensitive_to_inputs():
    a = RequestSpec(params={"misc": "yes", "b": "2"})
    b = RequestSpec(params={"b": "2", "misc": "yes"})
    assert cache_key(a) == cache_key(b)
    assert cache_key(RequestSpec(endpoint="https://other.test")) != cache_key(RequestSpec())
    assert cache_key(RequestSpec(params={"misc": "no"})) != cache_key(RequestSpec())


def test_invalid_cache_variants(tmp_path: Path):
    data_path, metadata_path = cache_paths(tmp_path, cache_key(RequestSpec()))
    assert not valid_cache(data_path, metadata_path, cache_key(RequestSpec()))
    data_path.write_text(json.dumps({"data": [{"id": 1}]}), encoding="utf-8")
    assert not valid_cache(data_path, metadata_path, cache_key(RequestSpec()))
    metadata_path.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
    assert not valid_cache(data_path, metadata_path, cache_key(RequestSpec()))
    metadata_path.write_text(json.dumps({"schema_version": "1", "cache_key": cache_key(RequestSpec()), "completed": True}), encoding="utf-8")
    assert valid_cache(data_path, metadata_path, cache_key(RequestSpec()))
    data_path.write_text(json.dumps({"data": []}), encoding="utf-8")
    assert not valid_cache(data_path, metadata_path, cache_key(RequestSpec()))


def test_success_saves_data_metadata_and_checksum(tmp_path: Path):
    client = FakeClient([response(payload={"data": [{"id": 1}]})])
    result = collect(tmp_path, client=client, sleep=lambda _: None)
    assert result["status"] == "fetched"
    assert result["record_count"] == 1
    data_path = Path(result["data_path"])
    metadata_path = Path(result["metadata_path"])
    assert data_path.exists() and metadata_path.exists()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["record_count"] == 1
    assert metadata["completed"] is True
    assert metadata["data_sha256"] == hashlib.sha256(data_path.read_bytes()).hexdigest()
    assert valid_cache(data_path, metadata_path, result["cache_key"])
    assert not list(tmp_path.glob(".*.tmp"))


def test_valid_cache_reused_and_force_fetches(tmp_path: Path):
    first = FakeClient([response(payload={"data": [{"id": 1}]})])
    collect(tmp_path, client=first)
    hit = FakeClient([])
    assert collect(tmp_path, client=hit)["status"] == "cache_hit"
    assert hit.calls == []
    forced = FakeClient([response(payload={"data": [{"id": 2}]})])
    assert collect(tmp_path, force=True, client=forced)["status"] == "fetched"
    assert len(forced.calls) == 1


@pytest.mark.parametrize("failure", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
def test_connection_and_timeout_retry_three_times(tmp_path, failure):
    client = FakeClient([failure, failure, failure])
    with pytest.raises(RuntimeError):
        collect(tmp_path, client=client, sleep=lambda _: None, jitter=lambda: 0)
    assert len(client.calls) == 3


def test_400_and_429_do_not_retry(tmp_path):
    for status in (400, 429):
        client = FakeClient([response(status=status, payload={"data": [{"id": 1}]})])
        with pytest.raises((RuntimeError, httpx.HTTPStatusError)):
            collect(tmp_path / str(status), client=client, sleep=lambda _: None)
        assert len(client.calls) == 1


def test_500_retries_with_injected_sleep(tmp_path):
    client = FakeClient([response(status=500, payload={"data": [{"id": 1}]})] * 2 + [response(payload={"data": [{"id": 1}]})])
    sleeps = []
    result = collect(tmp_path, client=client, sleep=sleeps.append, jitter=lambda: 0)
    assert result["attempts"] == 3
    assert sleeps == [1.0, 2.0]


def test_invalid_success_response_does_not_retry(tmp_path):
    client = FakeClient([response(payload={"items": []})])
    with pytest.raises(RuntimeError):
        collect(tmp_path, client=client, sleep=lambda _: None)
    assert len(client.calls) == 1
