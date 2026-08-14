# Issue #40 製品カテゴリと年別release countの探索的照合仕様

`analyze-release-factors` は、`analyze-releases` が出力した検証済みmetadata/JSONと、利用者が用意した出典付き製品カタログCSVを照合する。製品カテゴリがYGOPRODeck APIのカードrecordに含まれているとは仮定せず、API通信、スクレイピング、カテゴリの推測は行わない。

```text
ygonlp analyze-release-factors \
  --release-counts-metadata <release-counts-metadata-json> \
  --product-catalog <product-catalog-csv> \
  --output <output-directory> \
  --dry-run
```

## 入力カタログ

CSVはUTF-8で、次のheaderをこの順序で持つ。各行は一つのTCG製品を表し、`product_id`は一意である。

```csv
product_id,release_date,product_category,candidate_card_count,source_url,source_note
example-core-2020,2020-01-01,core_booster,100,https://example.invalid/product,official product page; counted first-appearance cards only
```

- `release_date` は製品のTCG発売日（`YYYY-MM-DD`）である。
- `product_category` はカタログ作成者が明示した、空でないカテゴリである。分析器は名称を公式分類へ変換しない。
- `candidate_card_count` は、その製品に割り当てた、対象datasetの**candidate TCG first appearance**カード数である。reprint、別製品との重複、印刷数は含めない。複数製品で同じカードを数えない責任はカタログ作成者にある。
- `source_url` は製品・発売日・内容数を確認できるHTTP(S)の出典、`source_note` は数え方または出典の補足である。

同一年のカタログ合計がdatasetの`release_count`を上回る場合は、重複または定義不一致とみなして失敗する。release-count artifactのUTC cutoff後に発売されたカタログ行は集計から除外し、`catalogue_future_date_row_count`として記録する。カタログにないカードは`uncatalogued_candidate_card_count`として残す。したがって、完全照合されていないカテゴリ別値を全release数の内訳と解釈してはならない。

## 出力と読み方

JSON、long-format CSV、Markdown、metadataをatomicに保存する。metadataはrelease-count artifactのchecksum、入力CSVのchecksum、カタログ行数、出力checksumを記録する。cache keyには両入力のchecksumと出力規則を含める。

年別表には次を含む。

- `year_over_year_change`: datasetのcandidate release countの前年差。最初の年は比較対象がないため`null`。
- `catalogued_product_count` と `active_product_category_count`: supplied catalogueに存在する製品・カテゴリ数。
- `catalogued_candidate_card_count`、`uncatalogued_candidate_card_count`、`catalogue_coverage_ratio`: カタログがcandidate first-appearance countをどこまで照合できたか。

カテゴリ表は、年とカテゴリ別の製品数・割り当てcandidate card数・同年`release_count`に対する割合を出す。部分年は入力release-count artifactの`is_partial_year`をそのまま保持する。カタログの製品日がrelease-count artifactの年範囲外またはUTC cutoffより後なら、カテゴリ表に混ぜず件数だけmetadata/JSONへ記録する。

## 制約

この照合は、カタログに記録された製品カテゴリとdataset上の年別candidate first appearancesの記述的な対応を示すだけである。カテゴリの登場、製品数、またはカテゴリ別カード数が年別release countの変化を引き起こしたことは示さない。製品の発表日・OCG発売日・TCG発売日、地域、reprintの扱い、カードの初出割当が異なると結果も変わる。Deck Build Packなどのカテゴリは、対象地域・期間・初出数を出典で確認した場合にのみ入力する。
