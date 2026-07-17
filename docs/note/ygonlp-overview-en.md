# A Reproducible Exploratory Analysis of 14,000 Yu-Gi-Oh! Card Texts

*A reproducible, exploratory look at text length, release counts, lexical patterns, and price snapshots with YGONLP.*

## Conclusion

I built YGONLP to turn a familiar player impression—“recent Yu-Gi-Oh! cards seem to have longer text”—into a reproducible question about card-text data. The project collects, validates, preprocesses, measures, and analyzes English-language Yu-Gi-Oh! OCG/TCG card records through a command-line workflow.

The current article reports Length Metrics: `character_count`, word count, and sentence count. It does not claim to measure gameplay complexity, rules difficulty, competitive strength, or card quality.

Among 13,179 measurement records with a usable candidate TCG first-appearance date at the UTC cutoff, average effect-text character count is generally higher in later years. The release-count series varies substantially from year to year rather than increasing monotonically. Lexical search, corpus vocabulary counts, and exploratory LDA provide additional ways to inspect the text collection; they do not turn card text into official mechanic labels or gameplay judgments. In the available price snapshot, text length and price do not show a clear, consistent relationship across vendors.

These descriptive, exploratory findings do not establish why text length changed, whether longer cards are stronger, or whether text length causes price.

## What I analyzed

### The question and the current scope

The initial research question was:

> How have Length Metrics in English Yu-Gi-Oh! effect text changed over time?

The current measurements are:

- `character_count`: characters in effect text
- `word_count`: words in effect text
- `sentence_count`: sentences in effect text

Possible future work could examine surface or structural features such as conditional clauses, costs, targeting, timing, and exception handling. Those are not reported as measured results in this article, and they are not part of the current Length Metrics scope.

### Records, dates, and analysis populations

The initial raw collection contained 14,468 records: 13,993 with `tcg_date` and 475 without it. The preprocessing target policy produced 13,431 measurement records. For the two yearly figures, 13,179 records had a valid `tcg_date` on or before the UTC cutoff of 2026-07-17. The yearly analyses exclude 234 records with `tcg_date=null` and 18 with a later date; missing dates are not inferred.

`tcg_date` is treated as a candidate TCG first-appearance date based on the adopted TCG set information. It is not a complete official release-history database. YGONLP does not fall back to an OCG date, invent a missing date, or substitute reprint or re-release dates, so this date field only supports the release-based analyses here.

The analyses do not all use the same population:

- Analyses 1 and 2 use the Length Metrics measurement records.
- Analyses 3 through 5 use 14,468 preprocessed records, with each method applying its own empty-document or tokenization exclusions.
- Analysis 6 joins a price snapshot to measurements by `card_id`; its counts are vendor-by-card observations rather than unique cards, and it is reported separately from the text-length analyses.

## Analysis results

### 1. Text length over time

For each candidate TCG first-appearance year, YGONLP summarizes the three Length Metrics using descriptive statistics: count, mean, median, minimum, maximum, first and third quartiles (`q1` and `q3`), and population standard deviation.

![Average effect-text character count by candidate TCG first-appearance year.](assets/text-length-character-count-trend.svg)

*Figure 1. Mean `character_count` by candidate TCG first-appearance year. The 2026 point is a partial year at the 2026-07-17 UTC cutoff.*

The series provides concrete reference points: the mean `character_count` was 134.138973 for 331 records in 2002, 203.705373 for 577 records in 2010, 417.811765 for 680 records in 2020, and 471.045714 for 525 records in 2025. The annual series is not uniform, but later years more often have higher average character counts, which is consistent with a long-term increase in text length. The 2026 partial-year value is 479.229064 across 406 records and is not used for full-year comparison.

This figure is descriptive. It does not identify why text length changed or imply a causal relationship with card strength, gameplay difficulty, game complexity, sales, or popularity.

### 2. Candidate first-appearance counts by year

`analyze-releases` counts candidate first appearances by year using `release_count` and a running `cumulative_release_count`; it can also break the result down by year and `card_type`.

![Candidate TCG first-appearance counts by year. Hatched bars indicate a partial year.](assets/yearly-release-count.svg)

*Figure 2. Overall `release_count` by candidate TCG first-appearance year. The hatched 2026 bar is partial through the 2026-07-17 UTC cutoff.*

The same 13,179 records yield 331 candidate first appearances in 2002, 577 in 2010, 680 in 2020, and 525 in 2025. Counts vary by year: the 2020 value, for example, is higher than the 2025 value, so the series is not a steady upward trend. The 406 records counted in 2026 represent only a partial year.

This is a count of candidate first appearances in this dataset, not a count of products, booster sets, printings, reprints, or a complete reconstruction of official release events. It provides context for the text-length series but does not identify the causes of changes in text length or extend its scope beyond release counts.

### 3. Finding lexically similar effect text

The similarity search represents normalized effect text using lowercase word-unigram TF-IDF vectors and ranks them by positive raw cosine similarity. It uses L2 normalization, IDF smoothing, no sublinear term frequency, and a Unicode-aware token pattern. The method is a lexical search, not a semantic embedding model or a comparison of rules processing.

For the example query, the input is 14,468 preprocessed records. The query card itself and empty text are excluded; the command returns the top 20 cards with positive raw cosine similarity. `tcg_date` is not a search filter—it only supplies release-status information displayed with a result.

In this representation, a higher score is associated with shared terms, similarly weighted vocabulary, and overlapping stock phrases. It does not directly measure word order or syntax, card-effect equivalence, compatibility, or competitive value, so it should be read as a lexical comparison within the current search scope.

#### Example: Astrograph Sorcerer

The verified search output queried Astrograph Sorcerer (card ID `76794549`) and returned the following top five matches.

| Rank | Similar card | Card type | Release status | Similarity score |
|---:|---|---|---|---:|
| 1 | Chronograph Sorcerer | Pendulum Effect Monster | released | 0.874417 |
| 2 | Astrograph Sorcerer, the Starfrost Magician | Pendulum Effect Monster | future_dated | 0.639925 |
| 3 | Odd-Eyes Arcray Dragon | Pendulum Effect Fusion Monster | released | 0.636476 |
| 4 | Supreme King Gate Zero | Pendulum Effect Monster | released | 0.635436 |
| 5 | Supreme King Z-ARC | Pendulum Effect Fusion Monster | released | 0.622448 |


`Chronograph Sorcerer` ranks first in this output. The second result has a `tcg_date` after the search cutoff and is therefore labeled `future_dated`; that status is metadata, not an input to the similarity score. The appearance of several Pendulum-related cards is compatible with shared vocabulary and recurring phrases, but does not imply equivalent effects or gameplay roles.

### 4. Frequently occurring vocabulary

YGONLP uses scikit-learn CountVectorizer for unigram and bigram frequency analysis, including document frequency: the number of card documents that contain a term at least once. The published example below is a whole-corpus unigram result, not a year-by-year or card-type comparison.

Its input is 14,468 preprocessed records. No document was empty; one record left no tokens after tokenization and was excluded, resulting in 14,467 documents. The example uses English stopword removal. Frequency is the total occurrence count in the corpus, while document frequency counts documents containing the term.

| Term | Frequency | Document frequency | Descriptive observation |
|---|---:|---:|---|
| `card` | 28,844 | 11,326 | References to cards occur broadly across the corpus. |
| `monster` | 23,492 | 10,672 | References to monsters occur broadly across the corpus. |
| `turn` | 13,502 | 8,764 | Turn-related wording appears in many card documents. |
| `effect` | 11,811 | 7,134 | The word effect occurs broadly. |
| `special` | 10,537 | 7,073 | The word special occurs broadly. |
| `summon` | 9,814 | 6,877 | The word summon occurs broadly. |
| `target` | 6,858 | 5,006 | Wording containing target appears in many card documents. |


Because this is unigram analysis, it does not show that `special` and `summon` occur adjacent to one another or in a particular construction. A verified bigram result would be needed to inspect those phrases. Frequency does not make a term an official mechanic classification, an indicator of importance, or a signal of card strength. Year- and `card_type`-specific trends likewise require separately verified grouped outputs, which are outside this corpus-wide example.

### 5. Exploratory vocabulary patterns with LDA

The topic analysis uses CountVectorizer and Latent Dirichlet Allocation (LDA) with a fixed random seed, an explicitly selected number of topics, and model-index ordering. It is an exploratory view of vocabulary distributions, not an official categorization system.

The LDA input is 14,468 preprocessed records. Zero empty documents and one tokenless document are excluded, leaving 14,467 documents for an eight-topic model. The analysis does not group the input by `tcg_date` or `card_type`.

A topic is a pattern of words that tend to co-occur in this model; it is not an official mechanic, archetype, gameplay role, or an inherently meaningful category. Topic indices are output positions, not ranks of importance. The example below preserves raw component weights for top words and raw document-topic proportions for representative cards, within this exploratory scope.

#### Example topics

**Topic 0**

| Top word | Raw component weight |
|---|---:|
| `atk` | 0.064518 |
| `fusion` | 0.044133 |
| `monsters` | 0.041155 |
| `card` | 0.040966 |
| `def` | 0.033937 |


| Representative card | Card ID | Raw document-topic proportion |
|---|---:|---:|
| Megarock Dragon | 71544954 | 0.970781 |
| Hidden Temples of Necrovalley | 70000776 | 0.958257 |
| Reptilianne Spawn | 21179143 | 0.956171 |


**Topic 1**

| Top word | Raw component weight |
|---|---:|
| `card` | 0.072445 |
| `1` | 0.071628 |
| `deck` | 0.070840 |
| `hand` | 0.064345 |
| `cards` | 0.062290 |


| Representative card | Card ID | Raw document-topic proportion |
|---|---:|---:|
| Sylvan Komushroomo | 99641328 | 0.975656 |
| Sylvan Lotuswain | 73136204 | 0.974969 |
| Spellbook Library of the Crescent | 40230018 | 0.974249 |


**Topic 7**

| Top word | Raw component weight |
|---|---:|
| `damage` | 0.085690 |
| `opponent` | 0.085470 |
| `monster` | 0.080965 |
| `card` | 0.074813 |
| `s` | 0.062327 |


| Representative card | Card ID | Raw document-topic proportion |
|---|---:|---:|
| Doble Passe | 79997591 | 0.972609 |
| Arcana Force XIV - Temperance | 60953118 | 0.971725 |
| Welcome to the Jungle | 300302076 | 0.968728 |


Tokens such as `1` and `s` are outputs of the current tokenization and `CountVectorizer` settings. They are preprocessing limitations, not game concepts. The examples are vocabulary-distribution patterns only.

### 6. Price snapshots and text length

The price analysis joins a vendor-specific price snapshot to Length Metrics by `card_id`. Vendors remain separate, and no currency conversion or cross-vendor merging is performed: Cardmarket is in EUR; TCGplayer, eBay, Amazon, and CoolStuffInc are in USD.

The price value is the minimum observed card-level price across versions for a vendor. It is not a price for a particular printing, edition, rarity, or condition. `0.00` is retained as an API-returned zero value rather than transformed into missing data, but it is excluded from statistics and correlations by default; `--include-zero` is required to include it, so the price analysis remains separate from the text metrics.

The verified snapshot at 2026-07-16 contains 72,340 vendor-by-card observations. Of these, 67,155 join to measurements. The default analysis excludes 6,928 joined API-returned zero values, leaving 60,227 nonzero vendor-by-card observations for the vendor-specific correlations with `character_count`.

| Vendor | Currency | Nonzero observations | Pearson | Spearman |
|---|---:|---:|---:|---:|
| Amazon | USD | 11,016 | 0.050700 | 0.103470 |
| Cardmarket | EUR | 13,198 | 0.001081 | 0.105573 |
| CoolStuffInc | USD | 11,530 | 0.023273 | 0.231893 |
| eBay | USD | 11,300 | -0.005576 | 0.013691 |
| TCGplayer | USD | 13,183 | -0.001765 | 0.104492 |


Pearson coefficients are close to zero for all vendors, whereas Spearman coefficients vary by vendor. CoolStuffInc has a Spearman's rank correlation coefficient of 0.231893, whereas eBay has 0.013691. Within this snapshot, the output does not show a clear, consistent relationship between text length and price across vendors. No causal interpretation follows from these correlations.

## What this analysis can and cannot tell us

All of these outputs are descriptive or exploratory analyses of their stated inputs.

- A longer effect text does not necessarily mean a stronger card. Effects, activation conditions, metagame fit, deck construction, adoption, and tournament results also matter.
- Text length alone does not identify gameplay difficulty or ruling difficulty. Some short cards are difficult to rule on, while some long cards are operationally simple.
- A time-series pattern does not explain its own cause. Design changes, rules maintenance, theme design, and product strategy are plausible possibilities, not conclusions of this analysis.
- TF-IDF similarity is lexical similarity, not proof of equivalent card effects, combos, deck membership, or competitive value.
- LDA topics are exploratory vocabulary patterns, not official mechanics or semantic labels.
- Price is affected by demand, supply, rarity, reprints, the competitive environment, and collecting demand. Those factors are not controlled for here, so text length cannot explain price on its own.

YGONLP is also not an evaluation of whether Yu-Gi-Oh! cards are fun, well designed, better in one era, or worse in another. Its purpose is to make the underlying text data inspectable with explicit definitions and limitations.

## Method and reproducibility

YGONLP provides command-line workflows for collection, preprocessing, measurement, yearly time-series summaries, yearly candidate release counts, TF-IDF similarity search, unigram/bigram vocabulary analysis, exploratory LDA, vendor-specific price snapshots, and price/text-length correlations.

![YGONLP's actual input dependencies. Collection feeds preprocessing, measurement, and summarization; analysis commands consume either preprocessing or measurement outputs as appropriate.](assets/analysis-pipeline-overview.svg)

*Figure 3. Pipeline and analysis-command dependencies. An arrow represents an input dependency; it does not mean that every analysis is downstream of `summary`.*

The source is the English-language Yu-Gi-Oh! OCG/TCG card data available through [YGOPRODeck API v7](https://db.ygoprodeck.com/api/v7/cardinfo.php). The initial collection is normally a single request. Downstream analysis consumes saved local data and does not contact the API again; collection conditions and timestamps are recorded in metadata.

Each stage treats metadata as an input boundary and records source files, checksums, record counts, timestamps, parameters, library versions, and generated files. JSONL key order, `card_id` order, aggregation order, rounding rules, and random seeds are fixed where applicable. Reproduction assumes the same code, dependencies, parameters, saved inputs, and UTC cutoff. Results can differ if an external API, a price snapshot, a library implementation, or the collection time changes.

Outputs are written atomically, and valid caches are preferred. `--force` bypasses a valid cache; `--dry-run` reports planned inputs, outputs, and cache state without network access or file changes. `verify-preprocess` checks schema and metadata consistency, while `cleanup-preprocess` defaults to listing candidates rather than deleting them. Fixed-input tests cover missing values, date handling, aggregations, output formats, and error paths without depending on external APIs or live data.

### Data rights and redistribution

Card names, card text, images, and prices are subject to the terms of their respective rights holders and data providers. This repository keeps code, specifications, and aggregate figures; it does not redistribute raw data, card text, price snapshots, or analysis CSV/JSONL files. Anyone obtaining or reusing data should independently review the terms of the YGOPRODeck API, each vendor's terms, and any applicable rights.

## Development notes

The difficult part of this project was not only implementing NLP methods. It was defining what each measurement means. “Release date,” for example, could mean an OCG debut, a TCG debut, a set release, or a reprint date. The choice changes the interpretation of a time series.

The project therefore makes its date definition, missing-data treatment, exclusions, and non-imputation policy explicit. It applies the same discipline to terms that otherwise sound intuitive: similarity refers to textual similarity under the stated representation; a topic is a vocabulary-distribution grouping; and complexity is only whatever a defined metric measures.

Possible next steps include more detailed structural text features, a comparison between Japanese and English card text, more advanced NLP representations, and applying the workflow to other large text collections. Any such extension should preserve the same question: what, exactly, does the number measure?

## AI tools disclosure

OpenAI’s ChatGPT, Codex, and GPT-Work were used during writing, implementation support, code review, and assistance with checking analysis outputs. The author reviewed and decided the analysis design, data-processing policy, implementation, interpretation, and material published in this article. Final responsibility for the article and its published artifacts remains with the author.
