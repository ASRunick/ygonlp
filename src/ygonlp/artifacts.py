"""共有する低水準のローカル成果物操作。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    """UTF-8 JSON artifactを読み込む。schema検証は呼び出し側が行う。"""
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def safe_child(directory: Path, name: Any) -> Path | None:
    """directory直下の通常ファイル名だけを安全に解決する。"""
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        return None
    relative = Path(name)
    if relative.name != name or ".." in relative.parts:
        return None
    candidate = directory / relative
    try:
        if candidate.resolve(strict=False).parent != directory.resolve(strict=False):
            return None
    except OSError:
        return None
    return candidate


def write_bytes_atomic(path: Path, content: bytes) -> None:
    """完全なbyte列を一時ファイル経由で置換する。"""
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
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def best_effort_unlink(path: Path) -> None:
    """rollback用に、削除失敗を隠して生成物を可能な限り消す。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
