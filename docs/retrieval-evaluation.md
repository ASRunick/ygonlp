# Offline retrieval investigation — Issue #63

## Decision and evidence boundary

Keep production semantic ranking unchanged. A hybrid improves several literal
surface proxies on this snapshot but regresses others. These proxies do **not**
establish semantic relevance, unconditional effects, cost correctness, rules
equivalence, or combo usefulness. No human semantic judgments were invented.
The research question is resolved with a reproducible comparison and a bounded
negative deployment decision; a representative human-judged set is still needed
before promoting rewriting, numeric filtering, or fusion to production.

Research baseline: `f461cb27c1b8a4308d17543dcf947b0c9e9c3937`.
The run used 13,462 cached card vectors and ten fixed query cases: nine surface
proxies and one explicitly `human_review_required` case. No model construction,
model download, card API, external LLM, or network access is performed by the
evaluation script. Model2Vec is not installed in the local evaluation environment.

## Reproduction

The tracked case definitions are in `tests/fixtures/retrieval-surface-cases.json`.
The large card corpus and existing caches are local inputs, not tracked artifacts.
On the campaign workstation:

```text
python scripts/evaluate_retrieval.py --preprocessing-metadata data/issue40/preprocessed/cards-normalized-6fb70c31c0582f0d.metadata.json --embedding-metadata data/issue61/embeddings/effect-embeddings-f349b14550e31beb.metadata.json --cases tests/fixtures/retrieval-surface-cases.json --query-cache data/issue61/search/query-embeddings --query-cache data/issue61/control-query-comparison-20260927-184710/take-control-psct/query-embeddings --output data/campaign/retrieval-k10-rrf60.json
```

Repeat with `--rrf-constant 10` and a separate output for sensitivity analysis.
Other machines must supply the **same** verified inputs to reproduce these numbers;
the tests use self-contained authored fixtures to verify software behavior offline.
An absent query cache fails explicitly rather than generating an embedding.

Input identities:

| Input | SHA-256 |
| --- | --- |
| Preprocessing metadata | `7ab6ca60793e38cf44f4747980493c1fbea2ecc5634b52d23c6cd845c0dd7c27` |
| Preprocessing JSONL | `edcb90240444c47753ed10e5bc6ed451dacef8fbe167e584811403f4dd9810b9` |
| Corpus NPY | `96a8d445ed1adcb03e5ab700af70fd76ca3b7741d9b9b6425f4f807acaa974cd` |
| Fixed case JSON (Git LF blob) | `8ce021a4c302122f5fff91c7416b5b9a4bb2fe6a5cad85ce1bf390616889108d` |

The report records the checksum of the **actual file bytes**. A Windows checkout
with Git CRLF conversion has case-file checksum
`3058ca1e61c313d1ffbd4f3272abbb7eb99481ec77bcb26e904609dbfecfb1cf`.
Both parse to the same fixed JSON judgments and produced identical rankings and
metrics. Compare the declared input bytes/line endings when auditing checksums.

The corpus records model `minishlab/potion-base-8M`, immutable revision
`bf8b056651a2c21b8d2565580b8569da283cab23`, Model2Vec `0.9.0`, 256 dimensions,
token mean pooling, L2 normalization, float32, and maximum 512 tokens. The research
adapter accepts the existing schema-1 snapshot **only** after checking its source
identity, artifact checksum, vectors, ID order/uniqueness, and card metadata against
the fully validated preprocessing source. Race comes from that exact source.
This does not make legacy artifacts valid for production `search-semantic`.

Output records source/model/cache identities, query embedding checksums, case
checksum, producer Git revision/dirty state, script and evaluation source checksums,
invocation, NumPy/scikit-learn versions, normalization, ranking, filters, and metrics.
Reports from a dirty checkout explicitly record that limitation rather than claiming
a clean released producer. Generated reports remain under ignored `data/campaign/`.

## Methods and metrics

All methods use the same embedded cards, so lexical results exclude cards omitted
from that corpus (including zero vectors). TF-IDF is fit once on that entire corpus
using the existing word-unigram vectorizer definition; filters restrict candidates
before top-k, not the fitting universe. Query text has whitespace collapsed and
trimmed; corpus text is the unchanged verified `text_normalized`.

- Semantic: raw cosine descending, including nonpositive similarities, as in production.
- Lexical: raw TF-IDF cosine descending; positive scores only.
- Metadata semantic: the same semantic scores restricted by explicitly supplied
  exact `card_type`/`race` constraints. This is **not** automatic natural-language extraction.
- Hybrid RRF: equal-weight reciprocal-rank fusion of the complete semantic and
  positive lexical lists, `sum(1/(constant + rank))`, with 1-based ranks.
- Normalized lexical: rewrite only the entire query `draw one..nine cards` to
  `draw 1..9 cards`. No prose-wide number substitution or effect parsing.
- Filtered hybrid RRF: restrict both ranked lists to the metadata candidates before fusion.

Every score tie is broken by ascending card ID. RRF combines ranks rather than
adding incompatible lexical and semantic score scales. Constant 60 is a declared
research control, not a tuned optimum; constant 10 is a sensitivity check.
The method follows [Cormack, Clarke and Büttcher (2009)](https://research.google/pubs/reciprocal-rank-fusion-outperforms-condorcet-and-individual-rank-learning-methods/).

Recall@10 divides retrieved positives by all positives in the fixed evaluation
universe. RR@10 is the reciprocal first-positive rank, or zero if none is retrieved;
its macro mean is MRR@10. nDCG@10 uses gain `2^grade-1`, discount `log2(rank+1)`, and
the ideal top-ten grades. The cumulative-gain approach is described by
[Järvelin and Kekäläinen (2002)](https://researchportal.tuni.fi/en/publications/cumulated-gain-based-evaluation-of-ir-techniques).
No-positive cases are undefined and excluded with a visible status/count;
human-review-required cases have no metrics. Authored fixtures and real surface
proxies are aggregated separately. Omitted IDs are grade zero only within the
explicitly exhaustive authored fixture or predicate-defined evaluation universe.

## Snapshot results

NumPy 2.1.2, scikit-learn 1.9.0; macro means across nine surface proxy cases:

| Method | Recall@10 | MRR@10 | nDCG@10 |
| --- | ---: | ---: | ---: |
| Semantic | 0.019332 | 0.546296 | 0.300121 |
| Lexical | 0.019942 | 0.555556 | 0.326340 |
| Metadata semantic | 0.026315 | 0.583333 | 0.333722 |
| Hybrid RRF, constant 60 | 0.021634 | 0.703704 | 0.383326 |
| Normalized lexical | 0.022588 | 0.555556 | 0.365047 |
| Filtered hybrid RRF, constant 60 | 0.035601 | 0.722222 | 0.434612 |

Large proxy positive sets explain the low recall values; they are not a measure
of the fraction of gameplay-relevant cards found. Some queries reuse embeddings
and proxy definitions, so these nine cases are not independent samples. The set
was chosen for count wording, explicit metadata, and known colloquial failure
cases, not as a random representative sample. No significance or corpus-wide
semantic-quality claim is made.

Per-query nDCG@10 (frozen judgments shared by paraphrase pairs):

| Case | Proxy positives | Semantic | Lexical | Metadata semantic | RRF | Normalized lexical | Filtered RRF |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| draw-digit | 168 | .6028 | .7421 | .6028 | .6653 | .7421 | .6653 |
| draw-word | 168 | .5392 | .3938 | .5392 | .6118 | .7421 | .6118 |
| draw-spell | 82 | .4671 | .4959 | .6595 | .4284 | .4959 | .7421 |
| summon-gy | 904 | .1518 | .3052 | .1518 | .2985 | .3052 | .2985 |
| revive-paraphrase | 904 | .3223 | .0000 | .3223 | .1795 | .0000 | .1795 |
| summon-spellcaster | 26 | .0000 | .0000 | .1100 | .0000 | .0000 | .1478 |
| steal-colloquial | 145 | .0000 | .0000 | .0000 | .0000 | .0000 | .0000 |
| control-psct | 145 | .1584 | 1.0000 | .1584 | .9364 | 1.0000 | .9364 |
| destroy-generic | 99 | .4595 | .0000 | .4595 | .3301 | .0000 | .3301 |

RRF regresses `revive-paraphrase`, `destroy-generic`, and unfiltered `draw-spell`
against semantic. It also regresses `control-psct` against lexical. None of the
methods resolves `steal-colloquial` at ten under its surface proxy. Count-word
normalization helps `draw-word`, but provides no evidence for general paraphrase
rewriting. Exact metadata constraints improve the constrained cases mechanically.
With constant 10, RRF nDCG@10 is 0.377398 and filtered RRF 0.427569: the macro
direction persists, but neither setting establishes semantic validity.

## Structured understanding and numeric limits

`understand_fixture_query` handles only `draw N cards` and
`discard N cards to draw M cards` (digits or English one through nine).
`parse_fixture_effect` handles only authored `Draw N cards.` and
`Discard N cards; draw M cards.` templates. Tests separate action/count from
discard cost, and show that embeddings with identical synthetic vectors cannot
distinguish counts. These vectors test software, not Model2Vec's measured behavior.
Conditions, alternatives, variable counts, multiple actions, zones, targets, and
timing remain unknown. Full-match grammars fail soft on unsupported/ambiguous
queries instead of treating a loose regex match as a verified hard constraint.

The explicit metadata prototype accepts only `card_type=X; race=Y; effect text`
with known exact metadata values. It preserves ordinary effect prose such as
`special summon a Spellcaster monster` and `destroy a spell card`, because those
may describe targets rather than the searched card. Repeated/conflicting or
unsupported clauses preserve the original query with no inferred filters.
The syntax is a research fixture, not a new CLI requirement.

Regex is useful for declared surface proxies and authored templates. A general
parser would require independently judged coverage for PSCT conditions, costs,
multiple clauses, and exceptions. Neither embeddings nor an LLM make extracted
hard constraints automatically correct; no LLM dependency was introduced.

Before a production search-quality change, obtain human judgments on actual
semantic relevance for count/cost distinctions, paraphrases, unsupported mechanics,
and the observed regressions. Use a held-out set with frozen provenance rather
than editing gold to favor a candidate. Current automatic evidence supports a
research harness and explicit metadata filtering, not automatic ruling interpretation.
