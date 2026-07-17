# Issue #29 年別カードrelease count仕様

`analyze-releases` は、measurement metadataを入力境界として年別のTCG release countを決定論的に出力する。API通信、因果推論、予測は行わない。

```text
ygonlp analyze-releases --input-metadata <measurement-metadata-json> --output <output-directory>
```

## 入力と日付定義

入力は `summarize.load_source()` によって完全検証されたmeasurement metadataおよびJSONLだけである。metadata schema、完了状態、metric定義、data file名、checksum、byte size、record count、JSONLの全record schemaと`card_id`昇順を検証する。

`tcg_date` はcandidate TCG first-appearance dateである。これはカードのprinting数またはreprint数ではない。欠損日付を推測せず、OCG日付へfallbackしない。`tcg_date=null` は除外して`missing_date_count`へ、UTC cutoffより後の日付は除外して`future_date_count`へ記録する。

## 集計と出力

集計対象が1件以上なら、最初のincluded yearからcutoff yearまでの全ての年を出力する。途中の0件年も含める。overallとyear×`card_type`の両方で、年昇順の`release_count`と年内累積の`cumulative_release_count`を出す。出力順はoverallのyear昇順、その後year×card_typeのyear昇順・card_type昇順である。

CSV fieldは次の固定順である。

```text
scope
year
card_type
is_partial_year
release_count
cumulative_release_count
```

cutoff年は、cutoffが12月31日より前の場合だけ`is_partial_year=true`である。12月31日のcutoffはfull yearでありfalseとする。集計対象が0件の場合、年を推測できないためoverallおよびyear×card_typeの行は空とする。

JSON、CSV、Markdown、metadataを同時にatomic保存する。cache keyにはmeasurement provenance、日付定義、cutoff、zero-year・partial-year規則、出力順を含める。metadataはchecksumとfile sizeを記録し、cache hit時にも全形式を検証する。保存失敗時は作成済みの新generationをbest effortでcleanupし、既存のvalid outputを削除しない。`--dry-run` は入力を検証するが出力directoryやファイルを作成せず、`--force` は有効cacheを無視して同一入力から決定論的に再生成する。
