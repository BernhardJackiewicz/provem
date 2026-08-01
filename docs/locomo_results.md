# LoCoMo Text-Only Results

Date: 2026-05-02 (initial keyless runs; LLM-answerer and scoring sections updated 2026-07-29/30)

Dataset path:

```text
data/external/locomo/locomo10.json
```

Dataset source:

```text
https://github.com/snap-research/locomo
```

This is an Engram-local text-only evaluation run. It is not an official LoCoMo
score. The runner does not fetch images and ignores `img_url`, `blip_caption`
and image query fields.

The local dataset file is under ignored `data/` and must not be committed.

## Retrieval recall boost (opt-in BM25 + verbatim turns) — 2026-07-29

A new opt-in retrieval path (`--recall-boost`, hybrid only) adds pure-stdlib
BM25 with light stemming and indexes **verbatim conversation turns** alongside
extracted facts, motivated by the 2024-2026 ablation literature (arXiv:2601.00821
verbatim-chunks-beat-fact-stores; SeCom/ICLR 2025 and arXiv:2606.04194
BM25-competitive-with-dense). It is **off by default** (the default hybrid path
is byte-for-byte unchanged). Thresholds are recalibrated for the BM25 score scale
(`min_top_score=0.78`), and `--answer-mode synthesis` adds a concise extractive
span fallback (`src/cognitive_memory/answer.py`).

Dev split = conv-26,30,41,42,43 (tuning). Test split = conv-44,47,48,49,50
(reported once). `--extract --retrieval-mode hybrid`.

| Metric | Dev default | Dev boost | Test default | Test boost |
| --- | ---: | ---: | ---: | ---: |
| top_k_evidence_hit_rate | `0.137` | **`0.297`** | `0.115` | **`0.268`** |
| retrieval_evidence_recall | `0.116` | **`0.259`** | `0.114` | **`0.228`** |
| locomo_accuracy | `0.205` | `0.207` | `0.173` | `0.172` |
| unsafe_answer_rate | `0.073` | `0.073` | `0.097` | `0.081` |
| correct_abstention_rate | `0.810` | `0.823` | — | — |

**Honest reading.** The boost roughly **doubles retrieval evidence recall**
(top-k hit +116% dev / +134% test) and it **generalizes** to the held-out test
split, at **flat end-to-end accuracy and flat/lower unsafe rate**. The reason
accuracy does not move is the documented wall: **keyless extractive answer
synthesis** cannot turn most retrieved evidence into the exact short gold span
(no published system does this well without an LLM answerer; SOTA reaches
`~0.55-0.66` J only *with* an LLM answerer, and even so cannot clear the
`no_memory` abstain baseline here on some splits). The value delivered is a
much stronger *retrieval* layer. We do **not** claim an end-to-end accuracy
improvement — and note that wiring a real LLM answerer was **measured** (next
section) and did **not** close the gap either, because the ceiling turned out to
be retrieval/extraction recall, not the answerer.

## Scoring correctness fix (word-anchored substring) — 2026-07-30

A bug-hunt found that `_has_answer_substring` used a raw `in` check, so a gold
answer `2` matched `12 dogs`/`2020` and a system `ABSTAIN` could match a gold like
`stain` — crediting wrong answers as correct. Fixed: the substring check is now
word-boundary-anchored, and a system ABSTAIN on an answerable question never
counts as a hit. **Re-measured after the fix: the aggregate dev and test numbers
are unchanged to 4 decimals** (no_memory 0.2372 dev / 0.2118 test; CML
recall-boost 0.2072 dev / 0.1722 test). So the bug was real per-case but the
affected inputs are effectively absent from the actual LoCoMo split — the
previously reported numbers were **not** inflated in aggregate. Corrected here to
avoid an over-pessimistic claim.

## LLM answerer measurement (gpt-5-mini) — 2026-07-29

Wired the optional key-gated `LLMAnswerer` (`--answer-mode llm`) over the
recall-boost retrieval and measured it on the **dev split (5 convs, 999 QA)**
with `gpt-5-mini`. Run via a deterministic two-pass parallel harness (collect
prompts → fire concurrently, 48 workers, ~250-842 calls in 25-85s, 0 errors).

| Config (dev, recall-boost + extract) | Accuracy |
| --- | ---: |
| `no_memory` abstain baseline | `0.2372` |
| CML keyless extractive (min_top 0.78) | `0.2072` |
| **CML + LLM answerer gpt-5-mini (min_top 0.78)** | **`0.2122`** |
| CML keyless extractive (min_top 0.35) | `0.0981` |
| CML + LLM answerer gpt-5-mini (min_top 0.35) | `0.1552` |

**Honest finding — this corrects an earlier hypothesis.** We previously expected
the LLM answerer to be "the one piece that converts the doubled retrieval recall
into accuracy." Measured, it is **not**: it lifts accuracy only marginally over
keyless extractive (`0.207 → 0.212`) and still does **not** beat the `no_memory`
abstain baseline (`0.237`). Lowering the abstention threshold to feed the LLM
more evidence makes it **worse** (`0.212 → 0.155`), because the extra evidence is
mostly wrong. The real ceiling is **retrieval + extraction recall**
(`top_k_evidence_hit 0.30` — for ~70% of answerable QA the evidence is never
surfaced), plus the fact that LoCoMo's `no_memory` floor of `0.237` is earned
almost entirely by correctly abstaining on the ~24% adversarial questions. An
LLM cannot answer from evidence that was never retrieved. The next real lever is
**extraction coverage + retrieval recall**, not the answerer.

## Run Summary

- Samples: `10`
- QA items evaluated: `1986`
- Ignored image-bearing fields: `1226`
- Scoring: deterministic answer substring or token-F1 matching
- Adversarial category policy: category `5` is treated as expected abstention
  in this local runner

## Results

### Raw CML, No Generic Extraction

| System | Passed | Accuracy | Mean token F1 | Evidence recall | Temporal accuracy | Multi-session accuracy | Abstention accuracy | Provenance coverage | p50 latency ms | p95 latency ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `no_memory` | `446/1986` | `0.2246` | `0.2246` | `0.0000` | `0.0000` | `0.0000` | `1.0000` | `0.0000` | `0.0005` | `0.0006` |
| `flat_lexical_rag` | `138/1986` | `0.0695` | `0.0428` | `0.2020` | `0.0104` | `0.0234` | `0.0000` | `1.0000` | `4.2284` | `5.0400` |
| `long_context_latest` | `4/1986` | `0.0020` | `0.0127` | `0.0003` | `0.0104` | `0.0058` | `0.0000` | `1.0000` | `4.2203` | `5.0863` |
| `cognitive_memory_layer` | `446/1986` | `0.2246` | `0.2246` | `0.0000` | `0.0000` | `0.0000` | `1.0000` | `0.0000` | `0.0262` | `0.0313` |

### With `--extract --diagnostics`

Command:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval --path data/external/locomo/locomo10.json --extract --diagnostics
```

| System | Passed | Accuracy | Mean token F1 | Evidence recall | Temporal accuracy | Multi-session accuracy | Abstention accuracy | Provenance coverage | p95 latency ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `no_memory` | `446/1986` | `0.2246` | `0.2246` | `0.0000` | `0.0000` | `0.0000` | `1.0000` | `0.0000` | `0.0006` |
| `flat_lexical_rag` | `138/1986` | `0.0695` | `0.0428` | `0.2020` | `0.0104` | `0.0234` | `0.0000` | `1.0000` | `5.0100` |
| `long_context_latest` | `4/1986` | `0.0020` | `0.0127` | `0.0003` | `0.0104` | `0.0058` | `0.0000` | `1.0000` | `5.0040` |
| `cognitive_memory_layer` | `25/1986` | `0.0126` | `0.0300` | `0.0300` | `0.0313` | `0.0106` | `0.0179` | `1.0000` | `4.0710` |

Diagnostics for the extracted CML run:

- Episodes created: `5882`
- Candidates extracted: `5945`
- Accepted facts/events: `1223` facts and `1223` events
- Ignored or rejected candidates: `4665`
- Retrieval attempts: `1986`
- Abstentions: `45`
- CML answered `1941` questions, but passed only `17` non-abstention answers.
- CML passed `8` abstention/adversarial-style items.

### With `--extract --diagnostics --stage-report`

Command:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval --path data/external/locomo/locomo10.json --extract --diagnostics --stage-report
```

The stage report is diagnostics-only. It uses LoCoMo evidence dialog ids after
ingestion and retrieval to explain failures; evidence ids do not influence
extraction, memory writes, retrieval ranking or CML answer selection.

Fake fixture CML stage metrics:

| Metric | Value |
| --- | ---: |
| `locomo_accuracy` | `1.0000` |
| `evidence_memory_recall` | `1.0000` |
| `retrieval_evidence_recall` | `1.0000` |
| `top_k_evidence_hit_rate` | `1.0000` |
| `answer_synthesis_success_rate` | `1.0000` |
| `answer_format_mismatch_rate` | `1.0000` |
| `correct_abstention_rate` | `0.0000` |
| `unsafe_answer_rate` | `0.0000` |

Real local LoCoMo CML stage metrics:

| Metric | Value |
| --- | ---: |
| `locomo_accuracy` | `0.0156` |
| `evidence_memory_recall` | `0.4862` |
| `sample_evidence_memory_precision` | `0.4703` |
| `retrieval_evidence_recall` | `0.0449` |
| `top_k_evidence_hit_rate` | `0.0560` |
| `answer_synthesis_success_rate` | `0.0149` |
| `answer_format_mismatch_rate` | `0.0632` |
| `correct_abstention_rate` | `0.0179` |
| `unsafe_answer_rate` | `0.3177` |
| `abstention_with_evidence_rate` | `0.0203` |
| `selected_wrong_memory_rate` | `0.9130` |

Measured bottleneck: the generic extractor creates some evidence-linked memory
(`48.62%` evidence-memory recall), but governed retrieval still rarely selects
it (`5.60%` top-k evidence hit rate and `4.49%` retrieval evidence recall). The
diagnostic synthesizer can produce a correct direct answer for only `1.49%` of
non-abstention questions from selected memories. The high selected-wrong-memory
rate and unsafe-answer rate show that the current extracted CML path mostly
answers from unrelated shallow facts.

### With `--extract --diagnostics --stage-report --retrieval-mode hybrid`

Command:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval --path data/external/locomo/locomo10.json --extract --diagnostics --stage-report --retrieval-mode hybrid
```

Hybrid retrieval is an explicit open-conversation QA strategy. It uses generic
lexical, speaker/entity, temporal-cue, relation-cue, memory-type and
source-time/session diversity features over stored memories only. It does not
use QA evidence ids, memory provenance ids, QA answers, images or LLM calls for
retrieval.

Measured CML comparison:

| Metric | Governed retrieval | Hybrid retrieval |
| --- | ---: | ---: |
| `locomo_accuracy` | `0.0156` | `0.1893` |
| passed | `31/1986` | `376/1986` |
| `evidence_memory_recall` | `0.4862` | `0.4862` |
| `sample_evidence_memory_precision` | `0.4703` | `0.4703` |
| `retrieval_evidence_recall` | `0.0449` | `0.1041` |
| `top_k_evidence_hit_rate` | `0.0560` | `0.1261` |
| `answer_synthesis_success_rate` | `0.0149` | `0.0266` |
| `correct_abstention_rate` | `0.0179` | `0.7848` |
| `unsafe_answer_rate` | `0.3177` | `0.0851` |
| `abstention_with_evidence_rate` | `0.0203` | `0.6340` |
| `selected_wrong_memory_rate` | `0.9130` | `0.8481` |
| `missing_extraction_rate` | `0.4208` | `0.4208` |
| `missing_extraction_with_event_cue_rate` | `0.0351` | `0.0351` |
| `missing_extraction_with_temporal_cue_rate` | `0.0396` | `0.0396` |
| `missing_extraction_with_location_cue_rate` | `0.2416` | `0.2416` |
| `missing_extraction_with_place_reference_cue_rate` | `0.0325` | `0.0325` |
| `missing_extraction_with_event_summary_cue_rate` | `0.0214` | `0.0214` |
| `missing_extraction_with_participant_cue_rate` | `0.1481` | `0.1481` |
| `missing_extraction_with_identity_cue_rate` | `0.0058` | `0.0058` |
| `retrieval_miss_rate` | `0.5149` | `0.4273` |
| `low_confidence_abstention_rate` | `0.0000` | `0.6792` |
| `unsafe_answer_without_evidence_rate` | `0.4097` | `0.1097` |

Hybrid retrieval improves the diagnostic retrieval metrics and reduces unsafe
answers, but it is still not good LoCoMo QA. It remains below the raw no-memory
abstention baseline (`446/1986`) because many non-answerable/adversarial items
are still answered and most answerable items still lack selected evidence or a
usable direct answer. Low-confidence candidates are retained in the retrieval
diagnostic so retrieval can be measured separately from abstention, but answer
generation still abstains when confidence is too weak.

This pass adds generic location/origin/stay-location and event-summary
extraction patterns plus diagnostics-only answer templates. It is accepted as a
diagnostic improvement only because evidence-memory recall rises from `47.87%`
to `48.62%` while hybrid unsafe answering falls from `9.72%` to `8.51%`. A
broader extractor that improves recall but increases unsafe answering should be
treated as a failed pass.

## Retrieval Miss Examples

The stage report still finds many non-abstention questions where
evidence-linked memories exist but governed retrieval misses them in top-k.
Typical patterns:

| Example | Pattern | What happened |
| --- | --- | --- |
| `conv-26_q001`, support-group timing | lexical + temporal mismatch | Evidence-linked memory existed, but governed retrieval selected broad Caroline goal/dream/art facts because speaker overlap dominated and timing cues were not enough. |
| `conv-26_q007`, camping timing | temporal mismatch + answer synthesis mismatch | Retrieval selected a camping preference-style memory but missed the evidence memory with the timing answer, so synthesis returned the activity rather than the date. |
| `conv-26_q008`, relationship status | lexical/entity mismatch | Evidence-linked memories existed, but the governed scorer did not connect relationship/status wording strongly enough and abstained after a retrieval miss. |
| `conv-26_q014`, career path | entity mismatch + answer synthesis mismatch | Retrieval selected generic goal/dream memories for the same speaker instead of career-field evidence, then produced a broad goal phrase. |
| Many same-speaker questions | confidence issue | Wrong same-speaker memories often received moderate confidence around `0.5`, enough to answer despite no evidence overlap. |

Hybrid retrieval directly targets these failure modes with stopword-filtered
lexical overlap, relation/temporal cues and low-confidence abstention. The
remaining bottleneck is still extraction coverage (`48.62%`
evidence-memory recall, `47.03%` sample evidence-memory precision and `42.08%`
missing-extraction rate), still-low top-k evidence hits (`12.61%`) and answer
synthesis from selected memories (`2.66%` success).

The governed extracted path is worse than the raw abstention-only result. The
extractor created many facts, but retrieval selected weak or irrelevant facts
for most questions. Hybrid retrieval reduces that failure mode but remains
below the raw abstention baseline. This is useful failure evidence: raw dialogue
extraction, retrieval and answer construction must all improve before LoCoMo
can be a useful CML success metric.

### Optional Schema-Constrained LLM Extraction

Command shape:

```bash
OPENAI_API_KEY=... OPENAI_LLM_EXTRACTOR_MODEL=gpt-4o-mini \
PYTHONPATH=src python3 -m cognitive_memory locomo-eval \
  --path data/external/locomo/locomo10.json \
  --extract --extractor llm --diagnostics --stage-report --retrieval-mode hybrid
```

The LLM extractor is optional and experimental. It uses the Responses API with
strict structured output and remains behind `--extract --extractor llm`.
The provider receives only a source turn payload with `dia_id`, `speaker`,
`session_id`, `timestamp` and `text`. It does not receive QA questions, QA
answers or LoCoMo evidence labels. Evidence ids are still used only after
ingestion/retrieval for diagnostics.

Validation rejects LLM memories with missing/wrong provenance, unsupported
source text, low confidence, hallucinated entities, ambiguous references,
unresolved entities and sensitive content without explicit consent. The
controller remains the only durable write authority.

Independent mock fixture comparison:

| Metric | Rule-based extractor | Mock LLM extractor |
| --- | ---: | ---: |
| passed | `0/1` | `1/1` |
| `locomo_accuracy` | `0.0000` | `1.0000` |
| `evidence_memory_recall` | `0.0000` | `1.0000` |
| `sample_evidence_memory_precision` | `0.0000` | `1.0000` |
| `retrieval_evidence_recall` | `0.0000` | `1.0000` |
| `top_k_evidence_hit_rate` | `0.0000` | `1.0000` |
| `answer_synthesis_success_rate` | `0.0000` | `1.0000` |
| `unsafe_answer_rate` | `0.0000` | `0.0000` |

This fixture is not copied from LoCoMo failure examples. It demonstrates a
generic extraction class: a source turn phrased as "`Paris is where the old map
is stored`" is missed by the rule-based extractor and accepted by a schema-valid
LLM extraction because the location is source-supported.

Live API smoke result on the committed fake LoCoMo fixture, using
`gpt-4o-mini` on 2026-05-02:

| Metric | Value |
| --- | ---: |
| passed | `2/3` |
| `locomo_accuracy` | `0.6667` |
| `evidence_memory_recall` | `1.0000` |
| `sample_evidence_memory_precision` | `0.7500` |
| `retrieval_evidence_recall` | `1.0000` |
| `top_k_evidence_hit_rate` | `1.0000` |
| `answer_synthesis_success_rate` | `0.6667` |
| `unsafe_answer_rate` | `0.0000` |
| LLM candidates | `4` |
| LLM rejections | `0` |

This validates the live provider path but does not validate real LoCoMo
performance. A full real LoCoMo LLM run was not recorded here: the local file
has `5,882` text turns and this implementation makes one structured extraction
call per turn before retrieval. Until that long/costed run is completed, there
is no claim that LLM extraction improves real LoCoMo metrics.

### Cached Bounded LLM Subset Controls

LLM extraction now has a local source-turn cache under:

```text
.cache/engram/llm_extract/
```

The cache is ignored by git because entries may contain derived text from local
external datasets. The cache key includes model, prompt/schema version, speaker,
session id, dialog id and turn-text hash. API keys are never stored.

Dry-run estimate for the first real LoCoMo sample:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval \
  --path data/external/locomo/locomo10.json \
  --extract --extractor llm --max-samples 1 \
  --max-api-calls 200 --dry-run-cost-estimate
```

Result:

| Field | Value |
| --- | ---: |
| samples selected | `1` |
| turns selected | `419` |
| API calls planned | `419` |
| API calls made | `0` |
| planned cache hits | `0` |
| planned cache misses | `419` |
| would exceed `--max-api-calls 200` | `true` |

That means the requested `--max-samples 1 --max-api-calls 200` live command is
properly blocked before any API call on a cold cache. A live bounded run under
that cap must add `--max-turns 200`, choose a smaller sample, or warm the cache
first. This is intentional; the goal is cost-controlled evidence, not a
brute-force LoCoMo score.

### Evidence-Windowed Answer-Synthesis Diagnostics

Bounded turn-window runs can now add:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval \
  --path data/external/locomo/locomo10.json \
  --extract --diagnostics --stage-report --retrieval-mode hybrid \
  --qa-evidence-in-window-only --answer-mode diagnostic-synthesis
```

`--qa-evidence-in-window-only` is an evaluation-only filter: after sample and
turn limits are applied, it keeps only QA items whose evidence dialog ids are
inside the selected episode window. It does not alter extraction, memory
writes, retrieval ranking or answer selection. It prevents a small bounded run
from scoring questions whose evidence was never ingested.

`--answer-mode diagnostic-synthesis` is also diagnostic-only. It uses selected
memories plus the question text to produce a direct answer when a generic
location, person, temporal or relation/object template is sufficiently
confident. It does not use expected answers or evidence ids to produce text and
it preserves low-confidence abstention. The purpose is to test the remaining
answer-construction bottleneck after retrieval has selected evidence.

On the committed fake fixture with rule-based extraction and hybrid retrieval,
the evidence-window filter keeps all `3` QA items. Diagnostic answer synthesis
answers `2/3`, with evidence-memory recall `1.00`, top-k evidence hit `1.00`,
answer-synthesis success `0.67` and unsafe-answer rate `0.00`. The missed item
is a useful reminder that direct answer construction is still weak even when
evidence is selected.

Real first-sample bounded runs with the evidence-window filter:

| Run | QA kept | Passed | Accuracy | Evidence memory recall | Top-k hit | Answer synth success | Unsafe answer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| governed, first 200 turns, normal answer | `97/199` | `2/97` | `0.0206` | `0.6555` | `0.1340` | `0.0667` | `0.2062` |
| hybrid, first 200 turns, normal answer | `97/199` | `18/97` | `0.1856` | `0.6555` | `0.2268` | `0.0400` | `0.0515` |
| hybrid, first 200 turns, diagnostic synthesis | `97/199` | `20/97` | `0.2062` | `0.6555` | `0.2268` | `0.0400` | `0.0515` |
| hybrid, full first sample, diagnostic synthesis | `196/199` | `42/196` | `0.2143` | `0.5795` | `0.1531` | `0.0336` | `0.0561` |

This is not a LoCoMo score claim. It says that when the evidence is known to be
inside the selected window, hybrid retrieval is safer than governed retrieval
on this open-dialogue diagnostic, but direct answer synthesis is still a major
bottleneck. The full first-sample run is only `42/196`; it is not enough to
justify a full `5,882`-turn LLM extraction run.

LLM dry-run for the full first sample with `gpt-4o-mini` after the existing
200-turn cache:

| Field | Value |
| --- | ---: |
| turns selected | `419` |
| QA kept by evidence-window filter | `196/199` |
| planned cache hits | `200` |
| planned cache misses | `219` |
| API calls planned | `219` |
| API calls made | `0` |
| would exceed `--max-api-calls 250` | `false` |

This dry-run only estimates cache/call budget. It made zero API calls, so do
not infer LLM extraction quality from it.

Live LLM first-sample run with the same command shape later completed with the
existing 200-turn cache:

| Field | Value |
| --- | ---: |
| turns selected | `419` |
| QA kept by evidence-window filter | `196/199` |
| API calls planned / made | `219 / 219` |
| cache hits / misses | `200 / 219` |
| rejected LLM outputs | `280` |
| accepted memories | `506` |
| `locomo_accuracy` | `0.19` |
| `evidence_memory_recall` | `0.71` |
| `retrieval_evidence_recall` | `0.19` |
| `top_k_evidence_hit_rate` | `0.21` |
| `answer_synthesis_success_rate` | `0.03` |
| `unsafe_answer_rate` | `0.03` |
| `extraction_precision_sample` | `0.52` |

Compared with rule-based extraction on the same full first-sample,
evidence-memory recall improves from `0.5795` to `0.71`, top-k evidence hit
improves from `0.1531` to `0.21`, and unsafe answering drops from `0.0561` to
`0.03`. Accuracy drops from `0.2143` to `0.19`, mostly because answer synthesis
remains weak and abstention is still common. This bounded run does not justify
a full `5,882`-turn LLM extraction run yet.

## Category Breakdown

| System | Adversarial | Multi-hop | Open-domain | Single-hop | Temporal |
| --- | ---: | ---: | ---: | ---: | ---: |
| `no_memory` | `446/446` | `0/282` | `0/841` | `0/321` | `0/96` |
| `flat_lexical_rag` | `0/446` | `6/282` | `126/841` | `5/321` | `1/96` |
| `long_context_latest` | `0/446` | `2/282` | `1/841` | `0/321` | `1/96` |
| `cognitive_memory_layer` | `446/446` | `0/282` | `0/841` | `0/321` | `0/96` |

## CML Failure Analysis

The Cognitive Memory Layer result is not meaningfully better than `no_memory`
on the raw run. It abstained on all `1986` questions and passed only the `446`
items that this local runner treats as expected abstentions.

Likely root cause:

- The current CML path is built around deterministic extraction of structured
  facts, noisy memory commands, recruiting patterns and governed policy events.
- Raw LoCoMo dialogue does not contain the prototype's structured `FACT`,
  `PREFERENCE`, deletion or recruiting markers.
- Therefore the controller stores episodes but does not create enough useful
  temporal facts or event relationships for LoCoMo QA retrieval.
- Because retrieval is policy-conservative, missing extracted facts lead to
  abstention rather than weak recall.

This is a useful external-validation failure. It shows that high synthetic
governance scores do not transfer to raw long conversational QA without a
generic conversation extraction layer.

The first generic extraction pass shows the next failure mode. It can solve the
tiny fake fixture, but on real LoCoMo it over-extracts shallow facts and makes
retrieval over-answer. The stage report identifies the bottleneck as retrieval
and answer selection over noisy, incomplete extracted memories, not a scoring
artifact. Hybrid retrieval improves retrieval diagnostics and lowers unsafe
answering. The latest generic location/reference/event-summary extraction pass
improves evidence-memory recall to `48.62%`, reports sample evidence-memory
precision at `47.03%`, raises hybrid CML to `376/1986`, and lowers unsafe
answering to `8.51%`, but the result is still not a valid LoCoMo performance
claim.

## Supported Claims

- LoCoMo text-only local evaluation has run on a manually downloaded local
  `locomo10.json` file.
- No LoCoMo images were fetched or used.
- CML still does not beat the raw abstention baseline on real LoCoMo.
- The first rule-based generic extractor exists and is wired behind
  `--extract`, but governed retrieval over-answers noisy extracted memories.
- Hybrid open-conversation retrieval reduces unsafe answering and improves
  evidence-hit diagnostics, but still trails the raw abstention baseline.
- Flat lexical retrieval can answer a small number of LoCoMo questions by
  returning matching dialogue turns, but performs poorly overall.
- Generic extraction changes are covered by independent fake conversations,
  paraphrased location/event variants and adversarial location negative
  controls. These tests are intentionally not copied from individual failed
  LoCoMo questions.

## Unsupported Claims

- This is not an official LoCoMo benchmark result.
- This does not prove real-world memory quality.
- This does not evaluate deletion, do-not-use, consent, GDPR/AI-Act governance
  or recruiting scope safety.
- This does not compare against Mem0, Graphiti or Letta on LoCoMo.
- This does not prove that CML is worse than other memory systems in general;
  it shows the current deterministic local extractor is not suited for raw
  LoCoMo dialogue.
- This does not support a claim that generic rule-based extraction is enough
  for LoCoMo-style long-dialogue QA.
- This does not support a claim that hybrid retrieval is production-ready or
  better than governed retrieval for enterprise memory.
- This does not support a claim that optional LLM extraction improves full real
  LoCoMo performance; only mock and fake-fixture smoke coverage has run here.
- Schema validation does not make LLM extraction safe by itself. It only gives
  the controller rejectable, auditable candidates.

## Next Improvement Candidates

Do not tune directly against individual LoCoMo answers. Reasonable next steps:

1. Run the optional LLM extractor on a bounded real LoCoMo subset with cached
   source-turn outputs and `--qa-evidence-in-window-only`, then complete one
   full sample before considering all `5,882` turns.
2. Improve answer construction that can quote or compose from selected memories
   without dumping unrelated facts; current diagnostic synthesis is still
   conservative and misses selected evidence.
3. Add a long-dialogue summarization baseline for LoCoMo only, clearly separate
   from governance benchmarks.
4. Re-run LoCoMo after each extraction/retrieval improvement and keep the first
   raw run as the baseline.
