# ygonlp

英語版『遊戯王OCG／TCG』カードテキストを対象とした、自然言語処理および記述的データ分析のための再現可能なCLI研究プロジェクトです。

## 研究目的

最初の研究課題は、次の問いです。

> 英語版遊戯王カードの効果テキストの長さと、言語的・構造的な複雑性は、時系列でどのように変化してきたか。

初期段階では、公開APIからカードデータを取得し、決定論的な前処理、測定、集計を行います。成果物はCSV、JSON、Markdown、またはターミナル上の表として出力します。

## データソースと発売時期

初期データソースには、公開されているYGOPRODeck APIを使用します。

初期マイルストーンでは、APIから取得可能なTCGセット発売日を使用します。カードが収録されたセットのうち最古の日付を、そのカードの「TCG上の初出候補」として導出します。これは確定的な公式初出日ではなく、データソースに基づく分析用の候補値です。

導出規則、日付欠損、プロモーションカード、再録セットしか確認できないカードの扱いを文書化します。OCG初出およびその他の地域・言語版の初出分析は、適切な追加データソースを確認するまで初期スコープ外とします。

取得日時、APIエンドポイント、取得条件、採用した日付定義、対象件数などを記録し、同じ条件で再取得できるCLIを目指します。

## 複雑性の定義

本プロジェクトでは、指標を次の3カテゴリに分けます。

### Length Metrics

- Characters
- Words
- Sentences

### Surface Complexity Metrics

- Colon count
- Semicolon count
- Parentheses count
- `once per turn` などの定型表現数

### Structural Metrics

- Condition count
- Cost count
- Target count
- Resolution count

本プロジェクトにおける「複雑性」は、Surface Complexity MetricsおよびStructural Metricsを指します。ゲームプレイ上の複雑性、ルール処理上の難しさ、競技的な強さを意味するものではありません。

したがって、カードテキストが長いことを、そのまま複雑なカードまたは強いカードと解釈してはいけません。

## CLI設計案

```text
ygonlp collect
ygonlp preprocess
ygonlp measure
ygonlp summarize
ygonlp export
```

保存先はCLI引数で指定します。

```text
ygonlp collect --output ~/data/ygonlp/raw
ygonlp collect --output ~/data/ygonlp/raw --force
ygonlp export --format markdown
ygonlp export --format csv
ygonlp export --format json
```

`export` は集計結果を機械可読なCSV、JSON、Markdownなどへ変換する将来拡張用のコマンドです。

## 初期リポジトリ構成

```text
ygonlp/
├── README.md
├── LICENSE
├── pyproject.toml
├── .gitignore
├── src/
│   └── ygonlp/
│       └── __init__.py
├── tests/
└── reports/
```

取得データおよび生成データはGit管理しません。`.gitignore`には次を含めます。

```gitignore
data/
reports/generated/
```

## 再現性とテスト

- Python 3.11以上を使用します。
- API取得条件、取得日時、キャッシュ利用状況を記録します。
- 発売時期の導出規則を記録します。
- 前処理と測定値計算は決定論的に実行できるようにします。
- 外部APIに依存しない固定入力テストを用意します。

## 初期スコープ外

- GUI、Webアプリケーション、Jupyter Notebook
- PNGやインタラクティブ可視化
- 日本語の形態素解析
- 大会環境の推定、カードの強さや勝率の予測、デッキ推薦
- 大規模なWikiクロール、全カードへのLLMアノテーション、価格分析
- GitHubアカウントや権限設定の変更

## ライセンス

ソースコードはMIT Licenseで公開します。取得データとカードテキストは、各データソースの規約および権利関係を別途確認します。
