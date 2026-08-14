# Issue #40: 年別candidate release countの要因探索

## 結論

2026-08-14 UTC cutoffの再取得では、2014年から2016年にかけて増加し、2019年に710件でこのseriesの最大値に達した後、2020年以降は年ごとの増減を伴いながら低下したことが確認できる。さらに、card-set一覧との一意な`set_name + tcg_date`照合により、年ごとのcandidate cardsの79.4%から100%を個別の製品名へ部分的に照合できた。これはcandidate TCG first appearancesの記述的な変化と製品候補の対応であり、製品戦略が原因であることを示さない。

このissueで追加した`analyze-release-factors`は、出典付きの製品カタログを入力すると、年別seriesとカテゴリ別のcandidate-card countを再現可能に照合する。未照合カード数とcutoff後の製品行を明示するため、カタログが不完全な状態でカテゴリ別値を全体の内訳として扱わない。

## 再現したrelease-count series

次の値は、2026-08-14にYGOPRODeck API v7から1回取得し、`collect → preprocess → measure → analyze-releases`を実行したローカル生成artifactに基づく。対象は`tcg_date`を持ち、cutoff以下のcandidate TCG first appearanceである。13,185件を含め、240件の欠損日付と37件のfuture-dated recordを除外した。生成データはGit管理しない。

| Period | Candidate release count | Change from prior year | Reading boundary |
|---|---:|---:|---|
| 2014 | 526 | -15 | Full year |
| 2015 | 611 | +85 | Full year |
| 2016 | 651 | +40 | Full year |
| 2017 | 632 | -19 | Full year |
| 2018 | 654 | +22 | Full year |
| 2019 | 710 | +56 | Full year; maximum in this snapshot |
| 2020 | 680 | -30 | Full year |
| 2021 | 626 | -54 | Full year |
| 2022 | 634 | +8 | Full year |
| 2023 | 629 | -5 | Full year |
| 2024 | 554 | -75 | Full year |
| 2025 | 525 | -29 | Full year |
| 2026 | 412 | — | Partial year; not comparable with full years |

従って、2015年以降は単調な増加ではない。2015–2016年と2018–2019年には増加区間があるが、2019年以降は2022年の小幅反発を除き、2025年まで低い値が続く。このseriesだけから構造変化の時点を自動決定したり、特定の製品形式の効果を推定したりしない。

## 実データによる製品名照合

YGOPRODeckの`cardsets.php` snapshot（1,032 set records）から、各measurement recordについて、候補日と一致する`card_sets.set_name`を探した。複数rarityによる重複を除き、**候補日と一致するset nameが一意な場合だけ**その製品へ割り当てた。0件または複数のset nameが候補になるカードは推測せず未照合とした。601製品行を作成し、UTC cutoff後の2製品行を除外した。

| Year | Release count | Unambiguously catalogued | Uncatalogued | Coverage |
|---|---:|---:|---:|---:|
| 2015 | 611 | 610 | 1 | 0.998363 |
| 2016 | 651 | 651 | 0 | 1.000000 |
| 2017 | 632 | 632 | 0 | 1.000000 |
| 2018 | 654 | 648 | 6 | 0.990826 |
| 2019 | 710 | 576 | 134 | 0.811268 |
| 2020 | 680 | 676 | 4 | 0.994118 |
| 2021 | 626 | 614 | 12 | 0.980831 |
| 2022 | 634 | 625 | 9 | 0.985804 |
| 2023 | 629 | 624 | 5 | 0.992051 |
| 2024 | 554 | 440 | 114 | 0.794224 |
| 2025 | 525 | 455 | 70 | 0.866667 |
| 2026 | 412 | 406 | 6 | 0.985437 |

最も多く一意照合された製品名は、2015年はCrossed Souls（98件）、2016年はInvasion: Vengeance（98件）、2017年はCircuit Break（97件）、2018年はCybernetic Horizon（97件）、2019年はDark Neostorm（97件）、2020年はEternity Code（97件）だった。2021–2023年はBurst of Destiny、Darkwing Blast、Age of Overlordが各100件、2024年はThe Infinite Forbiddenが101件、2025年はDoom of Dimensionsが98件だった。これらは各年に大きなcandidate-card群を持つ製品候補を示すが、全製品・全print・reprintを完全に再構成するものではない。

## 製品形式・イベントへの帰属が未確定である理由

raw API snapshotの`card_sets`要素は`set_name`、`set_code`、rarity、priceを持つが、公式の製品カテゴリ、イベントとの関係、初出カードの一意な製品割当は持たない。`misc_info.tcg_date`はカード単位のcandidate dateであり、製品IDではない。上の照合はset名と日付が一意に対応した範囲だけであり、2019年・2024年・2025年はcoverageが低いため、製品別比較の確度も年によって異なる。

Konamiの2019年の公式告知には、core booster、Structure Deck、Speed Duel、collector/premium productなど複数形式が同時期に存在したことが記載されている。たとえば[2019年2月の告知](https://www.konami.com/games/eu/fr/topics/14952/)はboosterとStructure Deckを、[2019年3月の告知](https://www.konami.com/games/eu/en/topics/14981/)はpremium boosterとSpeed Duelを説明している。この事実は「製品形式が複数あった」ことの証拠にはなるが、各形式が710件のcandidate cardsの何件を生んだか、または前年比+56の原因であったことの証拠にはならない。

同じ理由で、Deck Build Packのような名称・形式を2015年以降のTCG seriesへ機械的に対応付けない。地域、発売日、reprint、製品名の関係を製品単位で確認し、candidate first appearanceを重複なく数える必要がある。

## 次に必要な証拠と実行方法

製品形式・イベントを比較するには、製品単位の出典付きカタログを補強する。各行にはTCG発売日、公式または出典で定義されたカテゴリ、対象datasetに割り当てたcandidate first-appearance card数、製品ページ等の出典URL、数え方の注記を含める。固定CSV schemaと検証規則は[製品カテゴリ照合仕様](release-factor-analysis-spec.md)に定義している。

```text
ygonlp analyze-release-factors \
  --release-counts-metadata <release-counts-metadata-json> \
  --product-catalog <product-catalog.csv> \
  --output <directory>
```

出力は年別release count、カテゴリに割り当てられたcandidate-card count、未照合数、coverage ratioを同時に保存する。coverageが100%未満なら、そのカテゴリ値は部分的な照合結果として報告する。100%であっても、これは記述的な構成比であり、製品カテゴリやイベントが年別変化を引き起こしたという因果結論にはならない。
