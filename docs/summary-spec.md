# 測定済みテキスト指標の集計仕様

## 目的と範囲

`ygonlp summarize` はIssue #3のmeasurement metadataを入力境界として、測定済みカードのLength Metricsを決定論的な記述統計へ集計する。対象は `character_count`、`word_count`、`sentence_count` だけである。Surface Complexity Metrics、Structural Metrics、カード種別別集計、グラフ、HTML、notebook、API通信は対象外である。

## 依存と入力境界

runtime dependencyの正本は `pyproject.toml` の `numpy>=2.0,<3` である。`requirements.txt` と `requirements-dev.txt` はeditable install用の補助ファイルであり、`environment.yml` はPython 3.11とpipによるeditable development installを定義する。NumPy versionはsummary metadataにprovenanceとして記録するが、patch versionはcache keyに含めない。

入力はmeasurement metadataのみである。metadataはparse可能で、対応schema、`completed=true`、measurement cache key、metric identifier/version、source provenanceを持たなければならない。JSONL参照はmetadata directory内の安全な単純ファイル名に限定し、絶対パス、`..`、directory componentを拒否する。JSONLはregular file、checksum・size・record count一致、BOMなし、LF、recordごとのschema・strict ascending `card_id`・一意性を検証する。

すべての整数fieldではPythonの`bool`を拒否する。すなわち整数は `isinstance(value, int)` かつ `not isinstance(value, bool)` を満たし、countとsizeは非負である必要がある。

## Grouping

- `overall / all`: 全measurement record。
- `by_tcg_year`: `tcg_date` の先頭4桁ごと。
- `tcg_date=null`: `unknown` group。年を推測せず、OCG dateへfallbackしない。

数値年は昇順、`unknown` は最後に置く。measurement段階でtarget selectionは完了しているため、summarizeは再選択しない。0 recordは正常入力であり、overall countは0、year groupは空である。

## 統計

各group・metricに次を記録する。

- `count`、`minimum`、`maximum`: Python integer（空groupではminimum/maximumはnull）。
- `mean`: `numpy.mean`。
- `median`: `numpy.median`。
- `population_standard_deviation`: `numpy.std(ddof=0)`。
- `q1` / `q3`: `numpy.percentile(..., method="linear")` の25/75 percentile。

空groupではcount以外をnullにし、NumPyの空配列演算は実行しない。NumPy scalarはPython scalarへ変換し、NaN/Infinityを出力しない。floatは一箇所で小数6桁へroundし、negative zeroは`0.0`へ正規化する。CSVとMarkdownでは常に6桁固定表示する。

## 出力

canonical JSONを一度構築し、CSVとMarkdownは同じsummary objectから生成する。これによりgroup、metric、丸め済み統計値のcross-format consistencyを保つ。

- JSON: schema/version、source provenance、metric identifier/version、group/statistic definitions、overall、`by_tcg_year` を持つ。
- CSV: `scope,group,metric,count,mean,median,minimum,maximum,population_standard_deviation,q1,q3` のlong format。nullは空field。
- Markdown: CSVと同じ列と行順のtable。nullは `—`。

3形式はUTF-8 BOMなし、LF、最終LFありでserializeする。Markdown cellはbackslash、pipe、backtick、CR/LFをescapeまたは`<br>`へ正規化する。空入力でもJSON、CSV、Markdownを生成し、CSV/Markdownにはoverallの3 metric行を含める。

## Cacheとatomic保存

summary cache keyはsummary schema、source measurement key/checksum/count、metric identifier/version、grouping/statistic identifier、percentile method、ddof、precision、format順、unknown policyを含む。

generationは明示順 `json`、`csv`、`markdown` で `summary-<key-prefix>-<content-prefix>.*` に保存する。各generationをflush/fsyncしてatomic replaceし、3形式すべてを保存した後に固定metadataを最後にatomic replaceする。metadataはcommit pointerであり、未参照generationはvalid outputではない。失敗時は旧valid outputを維持し、cleanup失敗は根本例外を覆わない。

cache hitにはmetadataの全契約を再検証する。これにはschema/version、source metadata/data filenameとprovenance、metric identifier/version、grouping/statistic定義、percentile/ddof/precision、unknown/output ordering policy、format identifier、group count、各output filename/checksum/sizeを含む。3形式すべてが存在し、bytesがmetadataと一致しなければcache missである。

## CLIと失敗方針

```text
ygonlp summarize --input-metadata <measurement-metadata-json> --output <output-directory>
```

`--dry-run` は入力metadataと全JSONLを検証して計画を表示するが、output directory、temporary file、generation、metadataを作成せず、API通信もしない。`--force` はvalid cacheを無視して再集計するが、新metadataのcommitまで旧valid outputを削除しない。valid cacheかつforceなしでは再集計・書き込みを行わず、4ファイルのhashとmtimeを保持する。

metadata/JSONL破損、schema・型・順序・path・checksum不一致、計算不能、保存失敗はFatal errorである。0 recordと`unknown` groupは正常なデータ状態である。

## 制限

このMVPはLength Metricsだけを集計する。Surface/Structural Metrics、追加grouping、可視化、export専用機能、API通信は実装しない。
