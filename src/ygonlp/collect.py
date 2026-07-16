"""YGOPRODeck API v7からの、安全で再現可能なカードデータ収集。"""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import time
from dataclasses import dataclass, field
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
CONTENT_PREFIX_LENGTH = 16
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


class HttpClient(Protocol):
    def get(self, url: str, *, params: Mapping[str, str], timeout: Any) -> httpx.Response: ...


AtomicWriter = Callable[[Path, bytes], None]


@dataclass(frozen=True)
class RequestSpec:
    api_version: str = API_VERSION
    method: str = METHOD
    endpoint: str = ENDPOINT
    params: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_PARAMS))


def normalized_params(params: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """キャッシュキーに使う、順序非依存のquery parameters表現を返す。"""
    return tuple(sorted((str(key), str(value)) for key, value in params.items()))


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


def metadata_path(output: Path, key: str) -> Path:
    return output / f"cards-{key[:CACHE_PREFIX_LENGTH]}.metadata.json"


def generation_data_path(output: Path, key: str, content_sha256: str) -> Path:
    return output / f"cards-{key[:CACHE_PREFIX_LENGTH]}-{content_sha256[:CONTENT_PREFIX_LENGTH]}.json"


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _safe_data_path(output: Path, name: Any) -> Path | None:
    """metadataに記録されたファイル名をoutput配下の通常ファイルに限定する。"""
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        return None
    relative = Path(name)
    if relative.name != name or ".." in relative.parts:
        return None
    candidate = output / relative
    try:
        if candidate.resolve(strict=False).parent != output.resolve(strict=False):
            return None
    except OSError:
        return None
    return candidate


def valid_cache(output: Path, key: str) -> bool:
    """metadataポインタと、そのポインタ先の世代dataを検証する。"""
    try:
        metadata = _read_json(metadata_path(output, key))
        if not isinstance(metadata, dict):
            return False
        if metadata.get("schema_version") != SCHEMA_VERSION or metadata.get("cache_key") != key:
            return False
        if metadata.get("completed") is not True:
            return False
        data_path = _safe_data_path(output, metadata.get("data_file"))
        if data_path is None or not data_path.is_file():
            return False
        raw = data_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != metadata.get("data_sha256"):
            return False
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("data"), list) or not data["data"]:
            return False
        return len(data["data"]) == metadata.get("record_count")
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return False


def _validate_response(response: httpx.Response) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        raise httpx.HTTPStatusError("unexpected HTTP status", request=response.request, response=response)
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("response top level must be an object")
    if "data" not in payload:
        raise ValueError("response does not contain data")
    if not isinstance(payload["data"], list):
        raise ValueError("response data must be a list")
    if not payload["data"]:
        raise ValueError("response data must not be empty")
    return payload


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    """予測不能な一時ファイルを同一ディレクトリに置き、atomic replaceする。"""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
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


def _fetch_payload(
    spec: RequestSpec,
    client: HttpClient | None,
    sleep: Callable[[float], None],
    jitter: Callable[[], float],
) -> tuple[dict[str, Any], httpx.Response, int]:
    response: httpx.Response | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if client is None:
                with httpx.Client(timeout=TIMEOUT) as owned_client:
                    response = owned_client.get(spec.endpoint, params=dict(normalized_params(spec.params)), timeout=TIMEOUT)
            else:
                response = client.get(spec.endpoint, params=dict(normalized_params(spec.params)), timeout=TIMEOUT)
            if response.status_code == 429:
                raise RuntimeError("HTTP 429: レート制限です。最大1時間アクセスできない可能性があります。自動再送は行いません")
            return _validate_response(response), response, attempt
        except RuntimeError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError, ValueError) as exc:
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) or (
                isinstance(exc, httpx.HTTPStatusError) and response is not None and 500 <= response.status_code <= 599
            )
            if not retryable or attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"API取得に失敗しました（試行回数: {attempt}）") from exc
            sleep((2 ** (attempt - 1)) + jitter() * 0.1)
    raise AssertionError("unreachable")


def collect(
    output: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    spec: RequestSpec | None = None,
    client: HttpClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    writer: AtomicWriter = _write_bytes_atomic,
) -> dict[str, Any]:
    """取得またはキャッシュ再利用を行う。dry-runは読み取り以外の副作用を持たない。"""
    spec = spec or RequestSpec()
    key = cache_key(spec)
    metadata = metadata_path(output, key)
    hit = valid_cache(output, key)
    plan = {"cache_hit": hit, "cache_key": key, "metadata_path": str(metadata)}
    if dry_run:
        return plan
    if hit and not force:
        cached_metadata = _read_json(metadata)
        data = _safe_data_path(output, cached_metadata["data_file"])
        assert data is not None
        return {**plan, "status": "cache_hit", "attempts": 0, "data_path": str(data)}

    payload, response, attempts = _fetch_payload(spec, client, sleep, jitter)
    data_content = _json_bytes(payload)
    checksum = hashlib.sha256(data_content).hexdigest()
    data = generation_data_path(output, key, checksum)
    output.mkdir(parents=True, exist_ok=True)
    created_data = False
    try:
        if data.exists():
            if not data.is_file() or data.read_bytes() != data_content:
                raise OSError("同名のdata fileが期待する内容と一致しません")
        else:
            writer(data, data_content)
            created_data = True
        new_metadata = {
            "schema_version": SCHEMA_VERSION, "completed": True, "cache_key": key,
            "api_version": spec.api_version, "http_method": spec.method, "endpoint": spec.endpoint,
            "query_parameters": dict(normalized_params(spec.params)), "fetched_at": _utc_now(),
            "data_file": data.name, "record_count": len(payload["data"]), "request_attempt_count": attempts,
            "response_status_code": response.status_code, "response_content_type": response.headers.get("content-type"),
            "collector_version": "0.0.0", "data_sha256": checksum,
        }
        writer(metadata, _json_bytes(new_metadata))
    except OSError as exc:
        if created_data:
            data.unlink(missing_ok=True)
        raise RuntimeError("キャッシュ保存に失敗しました。既存キャッシュは変更していません") from exc
    return {**plan, "status": "fetched", "attempts": attempts, "record_count": len(payload["data"]), "data_path": str(data)}


def dry_run_lines(output: Path, *, force: bool = False, spec: RequestSpec | None = None) -> list[str]:
    spec = spec or RequestSpec()
    key = cache_key(spec)
    hit = valid_cache(output, key)
    params = "&".join(f"{key}={value}" for key, value in normalized_params(spec.params))
    return [
        f"HTTP method: {spec.method}", f"endpoint: {spec.endpoint}", f"query parameters: {params}",
        f"output directory: {output}",
        f"data file naming policy: cards-{key[:CACHE_PREFIX_LENGTH]}-<content-sha256-prefix>.json",
        f"metadata file path: {metadata_path(output, key)}", f"cache key: {key}",
        f"valid cache: {'yes' if hit else 'no'}",
        "API communication: yes (通常実行時)" if not hit or force else "API communication: no (valid cache)",
        f"--force: {'yes' if force else 'no'}", f"maximum request budget: {MAX_ATTEMPTS}",
    ]
