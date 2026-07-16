from pathlib import Path

from ygonlp.cli import main


def test_help(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["collect", "--help"])
    assert exc.value.code == 0
    assert "--dry-run" in capsys.readouterr().out


def test_output_is_required():
    try:
        main(["collect"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("argparse should reject missing output")


def test_dry_run_is_offline_and_does_not_change_files(tmp_path: Path, monkeypatch, capsys):
    import ygonlp.collect as module

    class NeverClient:
        def get(self, *args, **kwargs):
            raise AssertionError("HTTP client must not be called")

    monkeypatch.setattr(module, "HttpClient", NeverClient, raising=False)
    before = list(tmp_path.iterdir())
    assert main(["collect", "--output", str(tmp_path), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "API communication: yes" in output
    assert "maximum request budget: 3" in output
    assert list(tmp_path.iterdir()) == before


def test_force_dry_run_is_offline(tmp_path: Path, capsys):
    assert main(["collect", "--output", str(tmp_path), "--dry-run", "--force"]) == 0
    output = capsys.readouterr().out
    assert "--force: yes" in output
    assert "maximum request budget: 3" in output
