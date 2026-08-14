# Issue #40: 年別candidate release countの要因探索

## 結論

現在のYGONLP card datasetだけでは、年別candidate release countの増減を個々の製品、製品カテゴリ、またはイベントへ帰属させることはできない。一方で、2026-08-14 UTC cutoffの再取得では、2014年から2016年にかけて増加し、2019年に710件でこのseriesの最大値に達した後、2020年以降は年ごとの増減を伴いながら低下したことが確認できる。これはcandidate TCG first appearancesの記述的な変化であり、製品戦略が原因であることを示さない。

このissueで追加した`analyze-release-factors`は、出典付きの製品カタログを入力すると、上の年別seriesとカテゴリ別のcandidate-card countを再現可能に照合する。未照合カード数を明示するため、カタログが不完全な状態でカテゴリ別値を全体の内訳として扱わない。

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

## 製品・イベントへの帰属が未確定である理由

今回のraw API snapshotのカードrecordには`card_sets`があるが、その各要素は`set_name`、`set_code`、rarity、priceのみである。製品のTCG発売日、製品カテゴリ、初出カードの一意な製品割当、イベントとの関係は含まれない。analysisに採用している`misc_info.tcg_date`もカード単位のcandidate dateであり、製品IDではない。

Konamiの2019年の公式告知には、core booster、Structure Deck、Speed Duel、collector/premium productなど複数形式が同時期に存在したことが記載されている。たとえば[2019年2月の告知](https://www.konami.com/games/eu/fr/topics/14952/)はboosterとStructure Deckを、[2019年3月の告知](https://www.konami.com/games/eu/en/topics/14981/)はpremium boosterとSpeed Duelを説明している。この事実は「製品形式が複数あった」ことの証拠にはなるが、各形式が710件のcandidate cardsの何件を生んだか、または前年比+56の原因であったことの証拠にはならない。

同じ理由で、Deck Build Packのような名称・形式を2015年以降のTCG seriesへ機械的に対応付けない。地域、発売日、reprint、製品名の関係を製品単位で確認し、candidate first appearanceを重複なく数える必要がある。

## 次に必要な証拠と実行方法

製品単位の出典付きカタログを作成する。各行にはTCG発売日、カテゴリ、対象datasetに割り当てたcandidate first-appearance card数、製品ページ等の出典URL、数え方の注記を含める。固定CSV schemaと検証規則は[製品カテゴリ照合仕様](release-factor-analysis-spec.md)に定義している。

```text
ygonlp analyze-release-factors \
  --release-counts-metadata <release-counts-metadata-json> \
  --product-catalog <product-catalog.csv> \
  --output <directory>
```

出力は年別release count、カテゴリに割り当てられたcandidate-card count、未照合数、coverage ratioを同時に保存する。coverageが100%未満なら、そのカテゴリ値は部分的な照合結果として報告する。100%であっても、これは記述的な構成比であり、製品カテゴリやイベントが年別変化を引き起こしたという因果結論にはならない。
