"""YGOPRODeck API v7からの安全なカードデータ収集。"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import httpx

SCHEMA_VERSION = "1"
API_VERSION = "v7"
METHOD = "GET"
ENDPOINT = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
DEFAULT_PARAMS = {"misc": "yes"}
MAX_ATTEMPTS = 3
CACHE_PREFIX_LENGTH = 16
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


class HttpClient(Protocol):
    def get(self, url: str, *, params: Mapping[str, str], timeout: Any) -> httpx.Response: ...


@dataclass(frozen=True)
class RequestSpec:
    api_version: str = API_VERSION
    method: str = METHOD
    endpoint: str = ENDPOINT
    params: Mapping[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.params is None:
            object.__setattr__(self, "params", dict(DEFAULT_PARAMS))


def normalized_params(params: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in params.items()))


def cache_key(spec: RequestSpec) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "api_version": spec.api_version,
        "method": spec.method,
        "endpoint": spec.endpoint,
        "params": normalized_params(spec.params),
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cache_paths(output: Path, key: str) -> tuple[Path, Path]:
    prefix = key[:CACHE_PREFIX_LENGTH]
    data = output / f"cards-{prefix}.json"
    return data, output / f"cards-{prefix}.metadata.json"


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def valid_cache(data_path: Path, metadata_path: Path, key: str) -> bool:
    try:
        metadata = _read_json(metadata_path)
        data = _read_json(data_path)
        return (
            isinstance(metadata, dict)
            and metadata.get("schema_version") == SCHEMA_VERSION
            and metadata.get("cache_key") == key
            and metadata.get("completed") is True
            and isinstance(data, dict)
            and isinstance(data.get("data"), list)
            and bool(data["data"])
        )
    except (OSError, ValueError, TypeError):
        return False


def _validate_response(response: httpx.Response) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        raise httpx.HTTPStatusError("unexpected HTTP status", request=response.request, response=response)
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise ValueError("response data must be a non-empty list")
    return payload


def _write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def collect(
    output: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    spec: RequestSpec | None = None,
    client: HttpClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> dict[str, Any]:
    spec = spec or RequestSpec()
    key = cache_key(spec)
    data_path, metadata_path = cache_paths(output, key)
    cache_hit = valid_cache(data_path, metadata_path, key)
    plan = {"cache_hit": cache_hit, "cache_key": key, "data_path": str(data_path), "metadata_path": str(metadata_path)}
    if dry_run:
        return plan
    if cache_hit and not force:
        return {**plan, "status": "cache_hit", "attempts": 0}
    attempts = 0
    response: httpx.Response | None = None
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts = attempt
        try:
            if client is None:
                with httpx.Client(timeout=TIMEOUT) as owned_client:
                    response = owned_client.get(spec.endpoint, params=dict(normalized_params(spec.params)), timeout=TIMEOUT)
            else:
                response = client.get(spec.endpoint, params=dict(normalized_params(spec.params)), timeout=TIMEOUT)
            if response.status_code == 429:
                raise RuntimeError("HTTP 429: レート制限です。最大1時間アクセスできない可能性があります。自動再送は行いません")
            payload = _validate_response(response)
            break
        except RuntimeError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, httpx.HTTPStatusError, ValueError) as exc:
            last_error = exc
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)) or (
                isinstance(exc, httpx.HTTPStatusError) and response is not None and 500 <= response.status_code <= 599
            )
            if not retryable or attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"API取得に失敗しました（試行回数: {attempts}）") from exc
            sleep((2 ** (attempt - 1)) + jitter() * 0.1)
    else:
        raise RuntimeError("API取得に失敗しました") from last_error

    assert response is not None
    output.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(data_path, payload)
    saved_data = data_path.read_bytes()
    metadata = {
        "schema_version": SCHEMA_VERSION, "completed": True, "cache_key": key,
        "api_version": spec.api_version, "http_method": spec.method, "endpoint": spec.endpoint,
        "query_parameters": dict(normalized_params(spec.params)), "fetched_at": _utc_now(),
        "data_file": data_path.name, "record_count": len(payload["data"]), "request_attempt_count": attempts,
        "response_status_code": response.status_code, "response_content_type": response.headers.get("content-type"),
        "collector_version": "0.0.0", "data_sha256": hashlib.sha256(saved_data).hexdigest(),
    }
    _write_json_atomic(metadata_path, metadata)
    return {**plan, "status": "fetched", "attempts": attempts, "record_count": len(payload["data"])}


def dry_run_lines(output: Path, *, force: bool = False, spec: RequestSpec | None = None) -> list[str]:
    spec = spec or RequestSpec()
    key = cache_key(spec)
    data_path, metadata_path = cache_paths(output, key)
    hit = valid_cache(data_path, metadata_path, key)
    params = "&".join(f"{k}={v}" for k, v in normalized_params(spec.params))
    return [f"HTTP method: {spec.method}", f"endpoint: {spec.endpoint}", f"query parameters: {params}",
            f"output directory: {output}", f"data file path: {data_path}", f"metadata file path: {metadata_path}",
            f"cache key: {key}", f"valid cache: {'yes' if hit else 'no'}", "API communication: yes (通常実行時)" if not hit or force else "API communication: no (valid cache)",
            f"--force: {'yes' if force else 'no'}", f"maximum request budget: {MAX_ATTEMPTS}"]
