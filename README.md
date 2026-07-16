# ygonlp

英語版『遊戯王OCG／TCG』カードテキストを対象とした、自然言語処理および記述的データ分析のための再現可能なCLI研究プロジェクトです。

## 研究目的

最初の研究課題は、次の問いです。

> 英語版遊戯王カードの効果テキストの長さと、言語的・構造的な複雑性は、時系列でどのように変化してきたか。

初期段階では、公開APIからカードデータを取得し、決定論的な前処理、測定、集計を行います。成果物はCSV、JSON、Markdown、またはターミナル上の表として出力します。

## データソースと発売時期

初期データソースには、YGOPRODeck API v7を使用します。Card Information endpointは次のとおりです。

```text
https://db.ygoprodeck.com/api/v7/cardinfo.php
```

初期の全件取得では、原則として単一リクエストを使用します。追加情報取得のため、初期候補として `misc=yes` を使用します。公式レート制限は1秒あたり20リクエストであり、レート制限を超過すると1時間アクセスをブロックされる可能性があります。そのため、取得結果をローカル保存し、API呼び出しを最小化します。カードごとの逐次リクエスト、並列リクエスト、非同期による大量取得、自動ページ巡回、複数エンドポイントへの一括連続アクセスは初期スコープ外です。

初期マイルストーンでは、`misc=yes` で返るカード単位の `tcg_date` を「TCG初出日」の第一候補として使用します。`tcg_date` が欠損している場合は欠損として保持し、初期実装で推測しません。`ocg_date` は取得可能であれば保持してよいものの、初期分析の主対象にはしません。APIフィールドの意味と採用規則を文書化し、欠損値をゼロ、空文字、架空の日付へ変換しません。

収録セット一覧とセット発売日の最小値から初出候補を導出する方式は、初期の必須処理にはしません。セット情報からの導出は、欠損補完または整合性検証の将来候補として扱います。OCG初出およびその他の地域・言語版の初出分析は、適切な追加データソースを確認するまで初期スコープ外とします。

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
ygonlp collect --output ~/data/ygonlp/raw --dry-run
ygonlp export --format markdown
ygonlp export --format csv
ygonlp export --format json
```

`export` は集計結果を機械可読なCSV、JSON、Markdownなどへ変換する将来拡張用のコマンドです。

`collect --dry-run` はAPI通信およびファイル変更を行わず、HTTP method、正規化済みendpointとquery parameters、出力先、メタデータ保存先、キャッシュキー、利用可能なキャッシュ、通常実行時の通信有無、`--force` の適用結果、最大リクエスト予算を表示します。GET、HEAD、その他のネットワーク通信を含めて完全にオフラインで動作します。

収集データは世代別のdata fileとして保存し、固定パスのmetadataを有効キャッシュのコミットポインタとして扱います。metadataが参照するdata fileだけを有効とみなし、保存失敗時も既存の正常キャッシュを維持します。失敗時の未参照data fileは可能な限り削除し、残存しても有効キャッシュとしては扱いません。詳細仕様はIssue #1を参照してください。

## 前処理方針

前処理はIssue #1のraw metadataを入力として検証済みraw dataを解決し、1カード1recordのJSONLへ正規化します。全カードを保持したうえで、通常モンスターのフレーバーテキスト、Token、Skill Cardなどを `is_effect_text_target` と `exclusion_reason` で初期分析から区別します。

`text_raw` は原文を保持し、`text_normalized` では改行コードのLF統一と外側空白の除去だけを行います。Unicode normalization、句読点削除、小文字化、定型句の置換は行いません。時系列の第一候補は `misc_info` の `tcg_date` であり、欠損や不正な日付を推測・補完しません。

詳細なrecord schema、日付・重複・エラー方針、CLI、テスト計画は[前処理仕様](docs/preprocessing-spec.md)を参照してください。

```text
ygonlp preprocess --input-metadata <raw-metadata-json> --output <output-directory> --dry-run
ygonlp preprocess --input-metadata <raw-metadata-json> --output <output-directory>
```

## 基本テキスト指標

`measure` は前処理済みmetadataを入力境界として検証し、初期分析対象カードだけの決定論的なLength MetricsをJSONLで生成します。初期指標はUnicode code point数の `character_count`、Unicode-awareな固定regexによる `word_count`、終端記号分割による `sentence_count` です。Surface Complexity Metrics、Structural Metrics、集計はこの段階のスコープ外です。

```text
ygonlp measure --input-metadata <preprocessing-metadata-json> --output <output-directory> --dry-run
ygonlp measure --input-metadata <preprocessing-metadata-json> --output <output-directory>
```

`--dry-run` は前処理metadataとJSONLを読み取り・検証しますが、出力や一時ファイルを作成せず、API通信も行いません。詳細な指標定義、空出力、保存・検証方針は[測定仕様](docs/measurement-spec.md)を参照してください。

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
- API v7のendpoint、query parameters、リクエスト予算、タイムアウト、キャッシュ識別条件を記録します。
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
