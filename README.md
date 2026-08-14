# ygonlp

英語版『遊戯王OCG／TCG』カードテキストを対象とした、自然言語処理および記述的データ分析のための再現可能なCLI研究プロジェクトです。

## 動作確認済み環境

### macOS 26 (Apple Silicon)

以下の環境で動作確認しました。

- OS: macOS 26
- Hardware: Apple Silicon MacBook Air
- Python: 3.11.9

確認結果:

- 仮想環境 (`venv`) によるセットアップ: OK
- editable install (`pip install -e .`): OK
- テスト: 309 passed

macOS固有の問題は確認されませんでした。

## 研究目的

最初の研究課題は、次の問いです。

> 英語版遊戯王カードの効果テキストの長さと、言語的・構造的な複雑性は、時系列でどのように変化してきたか。

初期段階では、公開APIからカードデータを取得し、決定論的な前処理、測定、集計を行います。成果物はCSV、JSON、Markdown、またはターミナル上の表として出力します。

## 開発環境

Python 3.11を使用します。実行時の集計依存には `numpy>=2.0,<3` と `scikit-learn>=1.9,<2` を含みます。conda環境は次で作成できます。

```text
conda env create -f environment.yml
conda activate ygonlp
```

既存環境では、開発用editable installを使用します。

```text
python -m pip install -e ".[dev]"
```

## データソースと発売時期

初期データソースには、YGOPRODeck API v7を使用します。Card Information endpointは次のとおりです。

```text
https://db.ygoprodeck.com/api/v7/cardinfo.php
```

初期の全件取得では、原則として単一リクエストを使用します。追加情報取得のため、初期候補として `misc=yes` を使用します。公式レート制限は1秒あたり20リクエストであり、レート制限を超過すると1時間アクセスをブロックされる可能性があります。そのため、取得結果をローカル保存し、API呼び出しを最小化します。カードごとの逐次リクエスト、並列リクエスト、非同期による大量取得、自動ページ巡回、複数エンドポイントへの一括連続アクセスは初期スコープ外です。

初期マイルストーンでは、`misc=yes` で返るカード単位の `tcg_date` を、採用したTCG set情報に基づく「TCG初出候補日」として使用します。`tcg_date` が欠損している場合は欠損として保持し、初期実装で推測しません。`ocg_date` は取得可能であれば保持してよいものの、初期分析の主対象にはしません。APIフィールドの意味と採用規則を文書化し、欠損値をゼロ、空文字、架空の日付へ変換しません。

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

## 主なCLIコマンド

```text
ygonlp collect
ygonlp preprocess
ygonlp verify-preprocess
ygonlp cleanup-preprocess
ygonlp measure
ygonlp summarize
ygonlp analyze-timeseries
ygonlp analyze-releases
ygonlp analyze-archetypes
ygonlp analyze-archetype-similarity
ygonlp search-similar
ygonlp analyze-vocabulary
ygonlp analyze-topics
ygonlp snapshot-prices
ygonlp analyze-prices
```

保存先はCLI引数で指定します。

```text
ygonlp collect --output ~/data/ygonlp/raw
ygonlp collect --output ~/data/ygonlp/raw --force
ygonlp collect --output ~/data/ygonlp/raw --dry-run
```

`collect --dry-run` はAPI通信およびファイル変更を行わず、HTTP method、正規化済みendpointとquery parameters、出力先、メタデータ保存先、キャッシュキー、利用可能なキャッシュ、通常実行時の通信有無、`--force` の適用結果、最大リクエスト予算を表示します。GET、HEAD、その他のネットワーク通信を含めて完全にオフラインで動作します。

収集データは世代別のdata fileとして保存し、固定パスのmetadataを有効キャッシュのコミットポインタとして扱います。metadataが参照するdata fileだけを有効とみなし、保存失敗時も既存の正常キャッシュを維持します。失敗時の未参照data fileは可能な限り削除し、残存しても有効キャッシュとしては扱いません。詳細仕様はIssue #1を参照してください。

## 前処理方針

前処理はIssue #1のraw metadataを入力として検証済みraw dataを解決し、1カード1recordのJSONLへ正規化します。全カードを保持したうえで、通常モンスターのフレーバーテキスト、Token、Skill Cardなどを `is_effect_text_target` と `exclusion_reason` で初期分析から区別します。

`text_raw` は原文を保持し、`text_normalized` では改行コードのLF統一と外側空白の除去だけを行います。Unicode normalization、句読点削除、小文字化、定型句の置換は行いません。時系列の第一候補は `misc_info` の `tcg_date` であり、欠損や不正な日付を推測・補完しません。

詳細なrecord schema、日付・重複・エラー方針、CLI、テスト計画は[前処理仕様](docs/preprocessing-spec.md)を参照してください。

```text
ygonlp preprocess --input-metadata <raw-metadata-json> --output <output-directory> --dry-run
ygonlp preprocess --input-metadata <raw-metadata-json> --output <output-directory>
ygonlp verify-preprocess --input-metadata <preprocessing-metadata-json>
ygonlp cleanup-preprocess --output <preprocessing-output-directory>
ygonlp cleanup-preprocess --output <preprocessing-output-directory> --delete
```

`verify-preprocess` はAPI通信やファイル変更を行わず、前処理JSONLの全recordについてschema、固定キー順、`card_id` 昇順・一意性、metadataとのrecord count整合を検証します。

`cleanup-preprocess` は前処理output directory直下だけを調べ、metadataが参照していない世代別JSONLを検出します。デフォルトは候補を表示するだけのdry-runであり、削除は `--delete` を明示した場合だけです。metadata、ディレクトリ、symbolic link、および命名規則外のファイルは対象にしません。API通信は行いません。


## 基本テキスト指標

`measure` は前処理済みmetadataを入力境界として検証し、初期分析対象カードだけの決定論的なLength MetricsをJSONLで生成します。初期指標はUnicode code point数の `character_count`、Unicode-awareな固定regexによる `word_count`、終端記号分割による `sentence_count` です。Surface Complexity Metrics、Structural Metrics、集計はこの段階のスコープ外です。

```text
ygonlp measure --input-metadata <preprocessing-metadata-json> --output <output-directory> --dry-run
ygonlp measure --input-metadata <preprocessing-metadata-json> --output <output-directory>
```

`--dry-run` は前処理metadataとJSONLを読み取り・検証しますが、出力や一時ファイルを作成せず、API通信も行いません。詳細な指標定義、空出力、保存・検証方針は[測定仕様](docs/measurement-spec.md)を参照してください。

## テキスト指標の集計

`summarize` は `measure` が生成したmeasurement metadataを入力境界として検証し、`collect → preprocess → measure → summarize` の最後の集計段階を実行します。JSON、long-format CSV、Markdown tableを常に同時に生成します。全測定recordのoverall集計と、`tcg_date` の先頭4桁によるTCG初出候補年別集計を出力します。`tcg_date=null` は補完せず、`unknown` groupとして年の後ろに配置します。

```text
ygonlp summarize \
  --input-metadata <measurement-metadata-json> \
  --output <output-directory> \
  --dry-run
```

各metricは母標準偏差（NumPy `ddof=0`）とlinear percentileで集計し、浮動小数の出力は6桁に固定します。`summarize` は既存のmeasurement metadataとJSONLだけを読み、カードデータAPI通信を行いません。生成されたJSON、CSV、Markdown、metadataはGit管理対象に追加しません。詳細は[集計仕様](docs/summary-spec.md)を参照してください。

## TCG初出候補年別分析

`analyze-timeseries` はmeasurement metadataを入力境界として、既存の `character_count`、`word_count`、`sentence_count` をTCG初出候補年、および候補年×`card_type`で記述集計します。`tcg_date` は採用したTCG set情報に基づく初出候補日です。`tcg_date` 欠損とUTC実行日時点で未来の日付は補完せずreleased trendから除外し、件数をmetadataに記録します。JSON、CSV、Markdown、metadataを生成し、API通信は行いません。

各metricの年別meanとmedianについて、年とのPearson・Spearman相関と最小二乗直線のslope/interceptを算出します。2年未満、または相関における年別集計値が定数の場合は相関を`null`として理由を記録します。年・年別カード数・UTC cutoff・cutoff年が部分年かどうかも記録します。これは記述的な関連であり、因果関係や将来予測を示すものではありません。

```text
ygonlp analyze-timeseries --input-metadata <measurement-metadata-json> --output <output-directory> --dry-run
ygonlp analyze-timeseries --input-metadata <measurement-metadata-json> --output <output-directory>
```

この分析はカードテキスト長とTCG初出候補時期の記述的な関連を示すものであり、因果関係を推論するものではありません。

## 年別カードrelease count分析

`analyze-releases` はmeasurement metadataを入力境界として完全検証した測定JSONLから、TCG初出候補年ごとのカード数を集計します。`tcg_date` はcandidate TCG first-appearance dateであり、printing数やreprint数ではありません。欠損日付を推測せず、OCG日付へfallbackもしません。`tcg_date=null` とUTC cutoffより未来の日付は集計から除外し、それぞれの件数をmetadataに記録します。

```text
ygonlp analyze-releases --input-metadata <measurement-metadata-json> --output <output-directory> --dry-run
ygonlp analyze-releases --input-metadata <measurement-metadata-json> --output <output-directory>
```

出力は年別overallと年×`card_type`別の `release_count`、`cumulative_release_count` で、最初の集計対象年からcutoff年まで0件年も含みます。cutoff年はcutoffが12月31日より前の場合だけ `is_partial_year=true` です。JSON、CSV、Markdown、metadataを同時にatomic保存し、API通信は行いません。この記述集計は因果推論や予測を行いません。詳細は[release count仕様](docs/release-counts-spec.md)を参照してください。

## Archetype別テキストprofile分析

`analyze-archetypes` は前処理metadataを入力境界として検証済みJSONLを読み、archetypeを持つカードを対象に、カード数、正規化済みテキストの平均文字数・単語数・文数、および`card_type`分布を決定論的に集計します。archetypeが欠損したカードは推測・補完せず除外し、件数をmetadataに記録します。JSON、CSV、Markdown、metadataを同時にatomic保存し、API通信は行いません。

```text
ygonlp analyze-archetypes --input-metadata <preprocessing-metadata-json> --output <directory> --dry-run
ygonlp analyze-archetypes --input-metadata <preprocessing-metadata-json> --output <directory>
```

この分析はarchetype内のカードテキストの平均的なprofileを示すものであり、効果の意味的類似性、ゲームプレイ上の同等性、カード強度を示すものではありません。

## Archetype内テキスト類似性分析

`analyze-archetype-similarity` は検証済み前処理JSONLから、archetypeごとに効果テキスト対象かつ非空テキストのカードを比較します。`search-similar` と同じTF-IDF word unigram/cosine similarityで、正の生scoreの上位ペアをarchetypeごとに決定論的に出力します。archetype欠損、対象外カード、空テキスト、比較可能カードが1枚だけのarchetypeは除外件数としてmetadataへ記録します。JSON、CSV、Markdown、metadataを同時にatomic保存し、API通信は行いません。

```text
ygonlp analyze-archetype-similarity \
  --input-metadata <preprocessing-metadata-json> \
  --output <directory> \
  --top-n 10
```

この語彙的類似性は、意味的な同一性、ゲームプレイ上の等価性、カード強度を示すものではありません。

## 効果テキスト類似検索

`search-similar` は前処理metadataを入力境界として完全検証したJSONLだけを用い、API通信をせずに正規化済みテキストの語彙的な類似カードを検索します。queryは完全一致の `card_id` または完全一致の `name` のいずれか一方で指定します。同名カードが複数ある場合は曖昧として `card_id` を要求します。query自身と空テキストは除外し、重複テキストでもカードごとに別結果として保持します。

```text
ygonlp search-similar \
  --input-metadata <preprocessing-metadata-json> \
  --card-id <card-id> \
  --output <output-directory> \
  --top-n 10 \
  --card-type "Effect Monster" \
  --release-status released
```

比較には scikit-learn の `TfidfVectorizer`（Unicode token pattern、word unigram、lowercase、L2 normalization、IDF/smooth IDF、有効なsublinear TFなし、`float64`）と cosine similarity を使います。正の生cosine scoreだけを降順、完全に同値なら`card_id`昇順で返し、公開出力のscoreだけを6桁へ丸めます。JSON、CSV、Markdown、metadataは同時にatomic保存され、source checksum、query、filter、`top_n`、ranking定義、scikit-learn version、vectorizer classと主要parameterをmetadataに記録します。`--force` は有効なキャッシュを無視して再生成します。

この語彙的類似性は、意味的な同一性、ゲームプレイ上の等価性、カード強度、デッキ推薦を意味しません。

## 語彙・探索的トピック分析

`analyze-vocabulary` と `analyze-topics` は前処理metadataを完全検証した正規化JSONLだけを入力とし、API通信を行いません。空またはtokenなし文書を除外します。前者は scikit-learn `CountVectorizer` によるunigram/bigramの頻度・document frequency分析、後者は固定seedの `LatentDirichletAllocation` による探索的な語彙groupingです。

```text
ygonlp analyze-vocabulary --input-metadata <preprocessing-metadata-json> --output <directory> --ngram 2 --min-df 2 --english-stopwords
ygonlp analyze-topics --input-metadata <preprocessing-metadata-json> --output <directory> --topic-count 8 --random-seed 0 --max-iter 20
```

両コマンドはJSON、CSV、Markdown、metadataをatomicに保存し、source checksum、scikit-learn version、class名、主要parameter、ranking/orderを記録します。topic分析のJSONとCSVはcardごとの完全なtopic proportionを保持しますが、読みやすさのためMarkdownはtopic語、代表card、overall・card type・TCG年別のtopic prevalenceだけを掲載します。この包含方針もmetadataに記録します。LDA topicはmodel index順であり、意味的・公式のラベルは付けません。これは探索的な語彙分析であり、公式mechanic、意味的真実、ゲームプレイ上の同等性を示すものではありません。

## 価格snapshot

`snapshot-prices` はIssue #1の検証済みcollection metadataとraw JSONだけから、API通信なしでvendor別の価格snapshot JSONLを生成します。snapshot timestampはcollection metadataのUTC `fetched_at` です。`card_prices` の複数値はカード単位・vendor別のversion横断最小値として保存しますが、printing、edition、rarity、condition単位の価格を表すものではありません。

```text
ygonlp snapshot-prices --input-metadata <collection-metadata-json> --output <directory>
```

対応vendorはcardmarket（EUR）、tcgplayer / ebay / amazon / coolstuffinc（USD）です。通貨換算やvendor間の統合は行いません。価格はDecimalとして検証し、raw stringとdecimal stringをともに保存します。`"0.00"` は有効な観測値であり、zero flagを記録します。metadataの`missing_vendor_field_counts`は、全`card_prices` objectを確認してもnon-null値がない**card×vendor pair**数です。

## Snapshot価格分析

`analyze-prices` は検証済みprice snapshotとmeasurement metadataを`card_id`完全一致で結合し、API通信なしでvendor/currency別に記述統計と価格対テキスト長のPearson/Spearman相関を出力します。通貨換算・vendor統合は行いません。zero priceはcoverageに残し、既定では統計・相関から除外します。`--include-zero`でのみ分析へ含めます。coverageの`snapshot_zero_observation_count`はsnapshot全体、`joined_zero_observation_count`はmeasurementとjoinできた観測だけのzero件数です。

```text
ygonlp analyze-prices --price-metadata <price-snapshot-metadata-json> --measurement-metadata <measurement-metadata-json> --output <directory> --character-buckets 100,200,300,500
```

bucketは`0..最初の境界`、続いて`前境界+1..境界`、最後に`最終境界より大`（各上限を含む）です。TCG年別groupはsnapshot日時点で欠損または未来の候補日を除外します。Decimal価格は入力・join中に維持し、NumPy/SciPy統計・相関を実行する直前だけ有限`float64`へ変換します。相関は2件未満またはいずれかが定数ならundefined（`null`）であり、0にはしません。

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
- 大規模なWikiクロール、全カードへのLLMアノテーション
- GitHubアカウントや権限設定の変更

## ライセンス

ソースコードはMIT Licenseで公開します。取得データとカードテキストは、各データソースの規約および権利関係を別途確認します。
