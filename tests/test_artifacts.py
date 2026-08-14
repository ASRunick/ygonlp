from __future__ import annotations

from pathlib import Path

from ygonlp.artifacts import best_effort_unlink, read_json, safe_child, write_bytes_atomic


def test_json_artifact_helpers_preserve_utf8_and_safe_child_boundary(tmp_path: Path) -> None:
    target = tmp_path / "artifact.json"
    write_bytes_atomic(target, '{"name":"青眼"}\n'.encode("utf-8"))

    assert read_json(target) == {"name": "青眼"}
    assert safe_child(tmp_path, target.name) == target
    assert safe_child(tmp_path, "nested/artifact.json") is None
    assert safe_child(tmp_path, "../outside.json") is None
    assert safe_child(tmp_path, str(target.resolve())) is None


def test_atomic_write_replaces_existing_content_and_rollback_unlink_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"old")

    write_bytes_atomic(target, b"new")
    assert target.read_bytes() == b"new"

    best_effort_unlink(target)
    best_effort_unlink(target)
    assert not target.exists()
