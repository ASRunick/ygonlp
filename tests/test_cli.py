from pathlib import Path
import hashlib
import json

import pytest

from ygonlp.cli import main
from ygonlp.preprocess import preprocess
from ygonlp.measure import measure


def raw_source(tmp_path: Path) -> Path:
    raw = json.dumps({"data": [{
        "id": 1, "name": "Example", "type": "Effect Monster", "frameType": "effect",
        "race": "Warrior", "archetype": None, "desc": "Effect text",
        "misc_info": [{"has_effect": 1, "tcg_date": "2020-01-01", "ocg_date": "2019-01-01"}],
    }]}, ensure_ascii=False).encode("utf-8")
    data = tmp_path / "raw.json"
    data.write_bytes(raw)
    metadata = tmp_path / "raw.metadata.json"
    metadata.write_text(json.dumps({
        "schema_version": "1", "completed": True, "cache_key": "source-key",
        "data_file": data.name, "data_sha256": hashlib.sha256(raw).hexdigest(), "record_count": 1,
    }), encoding="utf-8")
    return metadata


def test_root_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "collect" in output and "preprocess" in output and "measure" in output and "summarize" in output and "cleanup-preprocess" in output and "analyze-timeseries" in output


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


def test_preprocess_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["preprocess", "--help"])
    assert exc.value.code == 0
    assert "--input-metadata" in capsys.readouterr().out


def test_preprocess_dry_run_requires_input_metadata(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["preprocess", "--output", "out", "--dry-run"])
    assert exc.value.code != 0
    assert "--input-metadata" in capsys.readouterr().err


@pytest.mark.parametrize(("force", "expected_force"), [(False, "no"), (True, "yes")])
def test_preprocess_dry_run_via_cli_is_read_only(tmp_path: Path, capsys, force: bool, expected_force: str):
    metadata = raw_source(tmp_path)
    output = tmp_path / "does-not-exist"
    arguments = ["preprocess", "--input-metadata", str(metadata), "--output", str(output), "--dry-run"]
    if force:
        arguments.append("--force")

    assert main(arguments) == 0
    text = capsys.readouterr().out
    for field in ["input metadata path:", "input record count:", "valid existing output:", "--force:", "conversion required:"]:
        assert field in text
    assert f"--force: {expected_force}" in text
    assert "conversion required: yes" in text
    assert not output.exists()


def test_preprocess_failure_returns_stderr(monkeypatch, tmp_path, capsys):
    import ygonlp.cli as cli

    monkeypatch.setattr(cli, "preprocess", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("前処理失敗")))
    assert main(["preprocess", "--input-metadata", str(tmp_path / "raw.metadata.json"), "--output", str(tmp_path / "out")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "前処理失敗" in captured.err


def test_measure_help_and_required_input_metadata(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["measure", "--help"])
    assert exc.value.code == 0
    assert "--input-metadata" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["measure", "--output", "out", "--dry-run"])
    assert exc.value.code != 0
    assert "--input-metadata" in capsys.readouterr().err


@pytest.mark.parametrize(("force", "expected_force"), [(False, "no"), (True, "yes")])
def test_measure_dry_run_via_cli_is_read_only(tmp_path: Path, capsys, force: bool, expected_force: str):
    raw_metadata = raw_source(tmp_path)
    preprocessed = preprocess(raw_metadata, tmp_path / "preprocessed")
    output = tmp_path / "does-not-exist"
    arguments = ["measure", "--input-metadata", str(preprocessed["output_metadata_path"]), "--output", str(output), "--dry-run"]
    if force:
        arguments.append("--force")

    assert main(arguments) == 0
    text = capsys.readouterr().out
    for field in ["input metadata path:", "measurement target count:", "excluded count:", "metric versions:", "measurement required:"]:
        assert field in text
    assert f"--force: {expected_force}" in text
    assert "measurement required: yes" in text
    assert not output.exists()


def test_measure_failure_returns_stderr(monkeypatch, tmp_path, capsys):
    import ygonlp.cli as cli

    monkeypatch.setattr(cli, "measure", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("測定失敗")))
    assert main(["measure", "--input-metadata", str(tmp_path / "input.metadata.json"), "--output", str(tmp_path / "out")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "測定失敗" in captured.err


def test_summarize_help_and_required_input_metadata(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["summarize", "--help"])
    assert exc.value.code == 0
    assert "--input-metadata" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["summarize", "--output", "out", "--dry-run"])
    assert exc.value.code != 0


def test_analyze_timeseries_help_and_required_input_metadata(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["analyze-timeseries", "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--input-metadata" in help_text and "TCG初出候補年" in help_text
    with pytest.raises(SystemExit) as exc:
        main(["analyze-timeseries", "--output", "out"])
    assert exc.value.code != 0
    assert "--input-metadata" in capsys.readouterr().err


def test_search_similar_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["search-similar", "--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert "--input-metadata" in text and "--card-id" in text and "--name" in text and "--force" in text


@pytest.mark.parametrize("command", ["analyze-vocabulary", "analyze-topics"])
def test_vocabulary_topic_help(capsys, command):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
    assert "--input-metadata" in capsys.readouterr().out


def test_summarize_dry_run_via_cli_is_read_only(tmp_path: Path, capsys):
    raw_metadata = raw_source(tmp_path)
    preprocessed = preprocess(raw_metadata, tmp_path / "preprocessed")
    measured = measure(preprocessed["output_metadata_path"], tmp_path / "measured")
    output = tmp_path / "does-not-exist"
    assert main(["summarize", "--input-metadata", str(measured["output_metadata_path"]), "--output", str(output), "--dry-run", "--force"]) == 0
    text = capsys.readouterr().out
    assert "overall count:" in text and "summary required: yes" in text
    assert not output.exists()
def test_verify_preprocess_via_cli(tmp_path: Path, capsys):
    raw_metadata = raw_source(tmp_path)
    preprocessed = preprocess(raw_metadata, tmp_path / "preprocessed")

    assert main([
        "verify-preprocess",
        "--input-metadata",
        str(preprocessed["output_metadata_path"]),
    ]) == 0

    output = capsys.readouterr().out
    assert "status: valid" in output
    assert "record count: 1" in output
    assert "preprocessing cache key:" in output


def test_verify_preprocess_requires_input_metadata(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["verify-preprocess"])

    assert exc.value.code != 0
    assert "--input-metadata" in capsys.readouterr().err


def test_cleanup_preprocess_cli_dry_run_delete_and_required_output(tmp_path: Path, capsys):
    raw_metadata = raw_source(tmp_path)
    output = tmp_path / "preprocessed"
    preprocess(raw_metadata, output)
    orphan = output / "cards-normalized-0123456789abcdef-fedcba9876543210.jsonl"
    orphan.write_text("orphan\n", encoding="utf-8")

    assert main(["cleanup-preprocess", "--output", str(output)]) == 0
    assert capsys.readouterr().out.strip() == str(orphan)
    assert orphan.exists()
    assert main(["cleanup-preprocess", "--output", str(output), "--delete"]) == 0
    assert capsys.readouterr().out.strip() == str(orphan)
    assert not orphan.exists()
    with pytest.raises(SystemExit) as exc:
        main(["cleanup-preprocess"])
    assert exc.value.code != 0
    assert "--output" in capsys.readouterr().err


def test_cleanup_preprocess_cli_failure_returns_stderr(monkeypatch, tmp_path, capsys):
    import ygonlp.cli as cli

    monkeypatch.setattr(cli, "cleanup_preprocess", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cleanup失敗")))
    assert main(["cleanup-preprocess", "--output", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cleanup失敗" in captured.err
