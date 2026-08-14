from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_documents_current_cli_scope():
    text = README.read_text(encoding="utf-8")

    assert "ygonlp export" not in text
    assert "ygonlp analyze-archetypes --input-metadata <preprocessing-metadata-json> --output <directory> --dry-run" in text
    assert "価格分析" not in text.split("## 初期スコープ外", maxsplit=1)[1]
