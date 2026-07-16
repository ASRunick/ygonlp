# Issue #2 前処理仕様

## 目的と範囲

Issue #1で取得したYGOPRODeck API v7のraw JSONを、後続の指標計算・集計用の安定した中間データへ変換する仕様である。本書はIssue #2 MVPの実装仕様を定める。

対象入力はCard Information endpointの `misc=yes` 付き全件取得結果である。入力raw JSON、取得metadata、出力中間データはいずれもGit管理しない。

## 調査結果（2026-07-16の統合取得）

- トップレベルはobject、`data` はlist、総件数は14,468件。
- `id`、`name`、`type`、`frameType`、`desc`、`race` は全件で存在し、それぞれintまたはstringだった。
- `archetype` は8,734件で存在し、5,734件で欠損した。
- `card_sets` は13,966件でlistとして存在し、502件で欠損した。存在時の要素数は1〜78件だった。
- `misc_info` は全件で要素数1のlistであり、要素はobjectだった。
- `tcg_date` は13,993件、`ocg_date` は13,923件で非空文字列だった。すべて `YYYY-MM-DD` 形式としてparse可能だった。
- `desc` は全件で非空stringだった。CRLFを含むものは2,393件、LFを含むものは3,579件、非ASCII文字を含むものは1,225件だった。
- 同一 `id` の重複はなかった。

この観測値は仕様の前提ではない。実装は欠損、不正型、複数の `misc_info` 要素、重複IDを安全に扱う。

## 対象カードの方針

### 採用: 案A（全カード保持）

すべての入力カードを1件ずつ中間形式へ保持し、`is_effect_text_target` と `exclusion_reason` で初期分析対象を明示する。

案B（前処理で除外）は出力を小さくできるが、通常モンスター、Token、Skill Card等を後から再評価できず、除外基準の変更も再取得・再前処理を要する。案Aは情報損失を抑え、対象範囲と解釈をレコード単位で監査できるため採用する。

`text_raw` と `text_normalized` は、対象外カードについても保持する。「テキストがある」と「効果テキスト分析対象」は別の概念である。

### `is_effect_text_target` の初期規則

初期版の研究対象は、APIの`desc`全体をカード単位の英語記述として扱う。Pendulum Effect、モンスター効果、その他の領域は推測分割しない。したがって、初期指標は個別効果領域ではなく、カード単位で提供された`desc`全体の記述的特徴を表す。

target policy versionは `1` とする。target type allowlistは次である。

```text
Effect Monster / Flip Effect Monster / Gemini Monster / Union Effect Monster
Spirit Monster / Toon Monster / Tuner Monster / Synchro Tuner Monster
Fusion Monster / Synchro Monster / XYZ Monster / Link Monster / Ritual Effect Monster
Pendulum Effect Monster / Pendulum Tuner Effect Monster / Pendulum Effect Fusion Monster
Pendulum Flip Effect Monster / XYZ Pendulum Effect Monster / Synchro Pendulum Effect Monster
Pendulum Effect Ritual Monster / Flip Tuner Effect Monster / Spell Card / Trap Card
```

対象判定は明示的なallowlistだけに基づく。allowlist内のMonsterは`has_effect == 1`を要求し、Spell CardとTrap Cardはallowlistだけで対象とする。unknown typeは保持するが、自動的に対象へ含めず、`is_effect_text_target=false`、`unknown_card_type` warningとして記録する。対象規則を変更するときはtarget policy versionを上げる。

優先順に次を適用する。

1. `desc` が欠損なら `false`、`exclusion_reason` は `missing_text`。
2. `desc` が空文字なら `false`、`exclusion_reason` は `empty_text`。
3. `type == "Token"` なら `false`、`token`。
4. `type == "Skill Card"` なら `false`、`skill_card`。
5. `frameType` が `normal` または `normal_pendulum` なら `false`、`normal_monster_flavor_text`。
6. allowlistに含まれるMonsterで`misc_info[0].has_effect` が整数 `1` ではないなら `false`、`not_in_target_policy`。
7. `type` が `Spell Card` または `Trap Card` なら `true`、理由はnull。
8. allowlistに含まれるMonsterで`has_effect == 1` なら `true`、理由はnull。
9. allowlist外の既知typeは `false`、`not_in_target_policy`。未知typeは `false`、`unknown_card_type`。

通常モンスター、Normal Tuner Monster、Pendulum Normal Monster、Ritual Monsterなど、effect flagを持たないモンスターの`desc`は保持するが初期効果テキスト分析から除外する。Pendulum系は分割・推測を行わず、対象なら結合された`desc`全体を1つのテキストとして扱う。TokenとSkill Cardも保持するが、初期研究対象からは除外する。

`text_kind` は `effect_or_rule_text`、`flavor_text`、`token_text`、`skill_text`、`unknown_text`、`missing_text` のいずれかとする。これはテキスト領域の厳密な意味解析ではない。

## 入力とCLI

初期MVPの入力はraw data fileではなく、Issue #1が生成したraw metadata fileとする。

```text
ygonlp preprocess --input-metadata <raw-metadata-json> --output <output-directory>
```

`--input-metadata` から `data_file` を安全に解決し、`completed`、schema version、cache key、SHA-256 checksum、record countを検証してからraw JSONを読む。これにより入力のprovenanceと完全性を必須にする。

`--input <raw-data-json>` を直接受け取る方式は初期MVPでは採用しない。metadataを伴わないrawファイルの直接利用は、provenanceの検証を弱めるためである。

`--dry-run` は入力metadataとraw dataを読み取り検証するが、output directory、一時ファイル、出力を作成しない。`--force` は有効な同一出力を無視して新世代を作る。どちらも実装時に追加する。

## 出力形式

### 採用: JSONL

1行を1カードrecordとするUTF-8 JSONLを採用する。改行を含む`text_raw`はJSON文字列としてescapeされ、record境界は安全に保たれる。

| 形式 | 判断 |
| --- | --- |
| JSONL | 採用。1カード1record、逐次処理、フィールド追加、欠損null、巨大テキストに適する。 |
| CSV | 不採用。改行・引用符を含むカードテキスト、配列・null、schema拡張の扱いが脆い。 |
| JSON配列 | 不採用。単一巨大documentとなり、逐次出力・差分確認・途中障害への扱いでJSONLより不利。 |

出力recordは`card_id`昇順で決定論的に並べる。raw APIの0始まり位置は`source_index`に保持する。日付順ではなくID順を選ぶのは、欠損日付や日付規則の変更によって出力順が変わらないためである。

## 中間record schema

固定バージョン値は、preprocessing schema version `1`、record schema version `1`、normalization version `1`、target policy version `1`、preprocessing metadata schema version `1` とする。すべてコード内の明示的定数であり、metadataへ記録する。欠損はJSON `null`、空文字は空文字として区別する。

| field | 型 | 必須 | source / 規則 | 用途 |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | 必須 | record schema定数 `1` | 互換性判定 |
| `card_id` | integer | 必須 | `id` | 一意キー、並び順 |
| `name` | string | 必須 | `name`、変更なし | 監査・極端値表示 |
| `card_type` | string | 必須 | `type`、変更なし | 種別比較 |
| `frame_type` | string | 必須 | `frameType`、変更なし | 種別比較・分類 |
| `race` | string/null | 必須 | `race`、不正型はnull+warning | 補助層別 |
| `archetype` | string/null | 必須 | `archetype` | 補助層別 |
| `text_raw` | string/null | 必須 | `desc`、変更なし | 原文監査 |
| `text_normalized` | string/null | 必須 | 下記正規化 | 測定入力 |
| `has_text` | boolean | 必須 | `text_raw` が非nullかつ非空 | 欠損区別 |
| `text_kind` | string | 必須 | 上記分類規則 | 解釈・除外監査 |
| `is_effect_text_target` | boolean | 必須 | 上記分類規則 | 初期分析filter |
| `exclusion_reason` | string/null | 必須 | 上記分類規則 | 対象外の理由 |
| `tcg_date` | string/null | 必須 | `misc_info[0].tcg_date` | 時系列主軸 |
| `ocg_date` | string/null | 必須 | `misc_info[0].ocg_date` | 保持のみ |
| `source_index` | integer | 必須 | raw `data`の0始まり位置 | raw順監査 |

`source_api_version`、source cache key、source file名、checksum等のprovenanceは各recordへ重複させず、ファイル単位metadataへ置く。

## テキスト正規化

`text_raw` はAPI文字列をそのまま保持する。大文字小文字、句読点、コロン、セミコロン、括弧、引用符、Unicode記号、文順は変更しない。

`text_normalized` は次だけを行う。

1. `\r\n` と単独の `\r` を `\n` に統一する。
2. 先頭・末尾のUnicode whitespaceを除去する。

内部の連続空白・改行は保持する。Unicode normalizationは初期段階では行わない。NFCは表記同一性を変える可能性があり、NFKCは互換文字や記号差を失わせる可能性があるためである。小文字化、句読点削除、定型句置換、HTML風記号の推測変換、文分割による上書きは行わない。

JSONLはUTF-8（BOMなし）、LF、1record 1line、最終LFありとする。record内のキー順はschemaで固定し、`ensure_ascii=False` とcompact separatorsを使う。同じ入力・同じversion設定ではJSONL bytesを一致させ、checksumは実際に保存したJSONL bytesのSHA-256とする。

## 日付

`tcg_date` をTCG初出日の第一候補、`ocg_date`を保持用の日付とする。入力が非空の`YYYY-MM-DD`としてparseできた場合のみ、同じ`YYYY-MM-DD`を出力する。日付はtimezoneを持たないcalendar dateであり、datetimeへ変換しない。

空文字・欠損はnullと`missing`、不正形式はnullと`invalid`にする。いずれもwarning集計に含め、ゼロ、空文字、架空の日付へ変換しない。

`misc_info` が0要素なら両日付をmissingとしてwarningにする。複数要素で非空・正常化可能な日付が1種類ならその値を採用する。複数の異なる日付値がある場合は日付を推測せずFatal errorとする。セット発売日の最小値による補完は行わない。

## 欠損、不正型、重複

- `archetype`、`tcg_date`、`ocg_date`、`desc`、`race` の欠損はnullまたは上記statusとし、warning集計へ記録する。
- `card_sets` は初期中間recordへ出力しない。欠損はwarning対象ではなく、セット由来日付の補完にも使わない。
- `id`、`name`、`type`、`frameType` が欠損または期待型でない場合はFatal errorとする。
- `race`、`archetype`、`desc` の不正型はnull+warningとする。
- 未知の`type`/`frameType`はrecordを保持し、`unknown_card_type`で初期分析から除外しwarning集計する。

`card_id`を一意キーとする。完全に同一のraw card objectが重複した場合のみ、最小`source_index`を残して決定論的に縮約する。同一IDで内容が異なる場合はFatal errorとする。

## 出力metadataと保存

出力JSONLは内容SHA-256を含む世代別ファイル名、固定metadataはその有効世代へのコミットポインタとする。data generationを同一ファイルシステム上のランダム一時ファイルへ書き、flush・可能な範囲のfsync・atomic replaceを行う。metadataはdata確定後に同様に確定する。保存失敗時、既存valid outputは維持する。

metadataは最低限、次を含む。

- metadata schema version、preprocessing schema version、record schema version、normalization version、target policy version、created at UTC
- source raw metadata file名、source raw data file名、source cache key、source checksum
- input record count、output record count、target count、exclusion reason別件数
- missing/invalid date数、missing text数、duplicate縮約数、unknown type数
- output generation file名、output checksum、sort order、normalization rules

metadataはsource data fileを安全に解決し、source checksumとrecord countを検証した場合のみ作成する。

## エラー、warning、終了コード

Fatal error（終了コード非ゼロ、stderr、出力metadataを確定しない）:

- raw metadata/raw JSONのparse不能
- raw metadataの安全性・checksum・record count検証失敗
- rawトップレベル/`data`構造不正
- 必須識別子の欠損・不正型
- 同一IDで異なる内容
- 複数`misc_info`由来の相反する日付
- output保存失敗

Warning（終了コード0、stderrには集約件数、出力metadataに機械可読な件数）:

- 任意フィールド欠損・不正型
- date欠損/不正形式
- 空/欠損text
- 未知card type
- 完全一致ID重複の縮約

## 実装テスト計画

- 正常最小入力、複数カード、card_id順JSONL、決定論的bytes
- 欠損・不正型・空text、Unicode、CRLF/LF、引用符、括弧、コロン、セミコロン
- Normal/Effect/Pendulum/Spell/Trap/Token/Skill/unknown種別の分類
- `tcg_date`/`ocg_date`の正常・欠損・空・不正・複数要素競合
- 完全一致重複の縮約と内容不一致重複のFatal
- raw metadata/data checksum、record count、パストラバーサル検証
- JSONL出力順、output metadata件数・checksum、atomic保存、保存失敗時の旧output保護
- `--dry-run` の副作用なし、`--force` 時の旧output保護

全テストは固定fixtureを用い、API通信を行わない。
