from pathlib import Path

import pytest

from ygonlp.cli import main


def test_root_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "collect" in capsys.readouterr().out


def test_collect_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["collect", "--help"])
    assert exc.value.code == 0
    assert "--dry-run" in capsys.readouterr().out


def test_output_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["collect"])
    assert exc.value.code != 0
    assert "--output" in capsys.readouterr().err


def test_dry_run_has_full_output_and_creates_nothing(tmp_path: Path, capsys):
    output = tmp_path / "does-not-exist"
    assert main(["collect", "--output", str(output), "--dry-run"]) == 0
    text = capsys.readouterr().out
    for field in ["HTTP method:", "endpoint:", "query parameters:", "output directory:", "data file naming policy:", "metadata file path:", "cache key:", "valid cache:", "API communication:", "--force:", "maximum request budget:"]:
        assert field in text
    assert not output.exists()


def test_force_dry_run_is_offline_and_creates_nothing(tmp_path: Path, capsys):
    output = tmp_path / "does-not-exist"
    assert main(["collect", "--output", str(output), "--dry-run", "--force"]) == 0
    assert "--force: yes" in capsys.readouterr().out
    assert not output.exists()


def test_collect_failure_returns_nonzero_and_writes_stderr(monkeypatch, tmp_path, capsys):
    import ygonlp.cli as cli
    monkeypatch.setattr(cli, "collect", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("失敗理由")))
    assert main(["collect", "--output", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "失敗理由" in captured.err
