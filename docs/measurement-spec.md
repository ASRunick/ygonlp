# Issue #3 基本テキスト指標仕様

この文書は、前処理済みカードJSONLから分析対象カードのLength Metricsを決定論的に生成するIssue #3 MVPの仕様である。Surface Complexity Metrics、Structural Metrics、`summarize`、`export` は対象外とする。API通信は行わない。

## 入力境界と対象選択

CLIは次のとおりである。

```text
ygonlp measure --input-metadata <preprocessing-metadata-json> --output <output-directory>
```

raw JSONLや前処理JSONLを直接入力にせず、Issue #2の前処理metadataを入力境界とする。metadata JSONのparse、対応するmetadata/record schema version、`completed=true`、source preprocessing cache key、`card_id_ascending` sort order、出力data file名、checksum、record countを検証する。data fileはmetadataと同じdirectory配下の単純ファイル名だけを許可し、絶対パス、`..`、directory componentを拒否する。

前処理JSONLは全行を検証する。各行は前処理record schema、必須fieldと型を満たし、`card_id`が厳密昇順で重複しない必要がある。BOM、CR、checksum不一致、parse不能、metadataとの件数不一致はFatal errorである。前処理の既存契約では0件JSONLは有効出力ではないため、測定入力としても受け付けない。

測定対象は、前処理recordの `is_effect_text_target` が `true` であり、`text_normalized` が空白だけではないstringであるカードだけである。前処理のtarget policyは再実装・再推測しない。対象外カードは測定JSONLに出力せず、metadataの `excluded_record_count` に含める。`is_effect_text_target=true` で空または空白だけのテキストは `empty_target_text_count` にも数える。

## バージョンとLength Metrics

初期値はすべて `1` とする。

- measurement record schema version
- measurement metadata schema version
- character metric version
- word metric version
- sentence metric version

### `character_count`

Pythonの `len(text_normalized)` と同じUnicode code point数である。空白、内部LF、句読点、記号、括弧、数字を含める。UTF-8 byte数やgrapheme cluster数ではなく、Unicode normalizationもしない。従って結合文字列 `e\u0301` は見た目にかかわらず2と数える。

### `word_count`

compile済みの次のregexのmatch数である。

```text
[A-Za-z0-9]+(?:['’,-][A-Za-z0-9]+)*
```

ASCII英字と数字を語本体とし、内部のASCII apostrophe、typographic apostrophe、hyphen、commaを許可する。これらが先頭・末尾だけにある場合は語に含めない。slash、colon、semicolon、period、question mark、括弧、改行、空白は区切りである。大文字小文字は変換せず、Unicode normalizationもしない。Unicode英字自体は語本体に含めないため、regexがASCII連続部分にだけ一致する場合がある。

例えば `X-Saber`、`once-per-turn`、`opponent's`、`opponent’s`、`1,000`、`1,000,000` は各1語、`ATK/DEF` と `Quick-Play Spell` は各2語である。

### `sentence_count`

`[.!?]+` で分割し、各断片を `strip()` して非空のものを数える。semicolon、colon、改行だけでは分割しない。略語・小数・カード固有表現の特別処理はしない。これは自然言語学的な文数ではなく、比較用の決定論的ヒューリスティックである。delimiterだけからなる非空テキスト（例: `...`）は0文、終端記号のない非空テキストは1文である。

## 出力

1対象カードを1行とするUTF-8 JSONLを出力する。recordは `card_id` 昇順で、次の固定順とする。

```text
schema_version
card_id
name
card_type
frame_type
tcg_date
text_normalized
character_count
word_count
sentence_count
```

`schema_version`、`card_id`、各countはinteger（countは0以上）。`name`、`card_type`、`frame_type`、`text_normalized`はstring。`tcg_date`はstringまたはnullである。JSONLはBOMなしUTF-8、LF、compact separators、`ensure_ascii=False`を用いる。1件以上なら最終LFを付ける。

対象が0件なら正常終了し、JSONLは0 bytes、output record countは0、checksumは空bytesのSHA-256とする。この空出力も有効なcache outputとして扱う。

## Cache、metadata、保存

measurement cache keyにはmeasurement metadata/record schema version、3つのmetric version、source preprocessing cache key、source preprocessing JSONL checksum、source record count、target selection rule、output format、sort orderを含める。同じ入力・定義では同じkey、metric versionまたは入力checksumが変われば異なるkeyとなる。

generation data fileは `cards-measured-<key-prefix>-<content-sha-prefix>.jsonl`、固定metadataは `cards-measured-<key-prefix>.metadata.json` とする。metadataはcommit pointerであり、少なくともversions、source provenance、入力・測定・除外・empty target textの件数、output file名・checksum・byte size、sort order、output format、metric identifier、project version、UTC作成時刻を記録する。

generation JSONLを一時ファイルへ保存してflush/fsyncしatomic replaceした後、metadataを同様に保存して最後にatomic replaceする。`--force`でも既存のvalid outputを先に削除しない。dataまたはmetadataの保存失敗時は既存のvalid outputを維持し、失敗した新generationのcleanupはbest effortで根本例外を覆わない。未参照generationはvalid outputではない。

`--dry-run` は入力metadataとJSONLを読み取り・検証し、対象数、除外数、出力先、命名規則、cache key、versions、既存valid output、`--force`、測定要否を表示する。ただし出力directory、一時ファイル、JSONL、metadataを作成・変更せず、API通信もしない。
