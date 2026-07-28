# LoCoMo Benchmark Readiness

LoCoMo is the Snap Research benchmark from "Evaluating Very Long-Term
Conversational Memory of LLM Agents." The public repository describes ten
very long multi-session conversations in `data/locomo10.json`, with annotated
question-answer pairs and event summaries.

This repo implements a **local text-only QA runner** for LoCoMo readiness. It
does not download the dataset, does not use images and does not prove official
LoCoMo performance. The default and rule-based modes make no external API
calls. The optional `--extractor llm` mode can call a configured LLM provider
only when explicitly requested. Local real-dataset runs are diagnostic evidence
only.

## What This Runner Measures

The local runner evaluates LoCoMo QA annotations against the same local baseline
style used elsewhere in Engram:

- `no_memory`
- `flat_lexical_rag`
- `long_context_latest`
- `cognitive_memory_layer`

It reports:

- `locomo_qa_accuracy`
- `mean_token_f1`
- `evidence_recall`
- `temporal_question_accuracy`
- `multi_session_question_accuracy`
- `abstention_accuracy`
- `provenance_coverage`
- `p50_latency_ms`
- `p95_latency_ms`

The scoring is deterministic and approximate. It uses answer substring matching
or token F1, not the original LoCoMo evaluation scripts. Results from this
runner must be labeled as Engram-local LoCoMo QA readiness results, not as
official LoCoMo benchmark scores.

## Dataset Handling

Expected local path:

```text
data/external/locomo/locomo10.json
```

`data/` is ignored by git. Do not commit `locomo10.json`.

The dataset source is:

```text
https://github.com/snap-research/locomo
```

LoCoMo is released under CC BY-NC 4.0 in the upstream repository. Verify license
compatibility before using it in any commercial or product context.

## Text-Only Mapping

The `LoCoMoLoader` maps:

- `sample_id` -> sample/user scope
- `conversation.session_<n>` turns -> local `Episode` objects
- turn `dia_id` -> episode id and provenance id
- turn `speaker` and `text` -> episode actor/content
- `session_<n>_date_time` -> episode timestamp when parseable
- `qa` annotations -> evaluation questions
- `qa.evidence` -> expected provenance ids where available

The loader deliberately ignores:

- `img_url`
- `blip_caption`
- image search `query`
- multimodal dialog generation fields
- event-summary scoring

No image URL is fetched. BLIP captions are not used for MVP readiness because
that would mix generated image descriptions into a text-only memory evaluation.

## CLI

Run the committed fake fixture:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval --path tests/fixtures/locomo/fake_locomo.json
```

Run a manually downloaded local LoCoMo file:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval --path data/external/locomo/locomo10.json
```

Run with the early generic conversation extractor and diagnostics:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval --path data/external/locomo/locomo10.json --extract --diagnostics
```

Run the diagnostic stage report:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval --path data/external/locomo/locomo10.json --extract --diagnostics --stage-report
```

Run the open-conversation hybrid retrieval diagnostic:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval --path data/external/locomo/locomo10.json --extract --diagnostics --stage-report --retrieval-mode hybrid
```

Run an evidence-windowed diagnostic answer-synthesis evaluation:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval \
  --path data/external/locomo/locomo10.json \
  --extract --diagnostics --stage-report --retrieval-mode hybrid \
  --qa-evidence-in-window-only --answer-mode diagnostic-synthesis
```

Run the optional schema-constrained LLM extractor:

```bash
OPENAI_API_KEY=... OPENAI_LLM_EXTRACTOR_MODEL=gpt-4o-mini \
PYTHONPATH=src python3 -m cognitive_memory locomo-eval \
  --path data/external/locomo/locomo10.json \
  --extract --extractor llm --max-samples 1 --max-api-calls 200 \
  --diagnostics --stage-report --retrieval-mode hybrid
```

Estimate LLM extraction cost without making API calls:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval \
  --path data/external/locomo/locomo10.json \
  --extract --extractor llm --max-samples 1 --dry-run-cost-estimate
```

Run through an approved manifest:

```bash
PYTHONPATH=src python3 -m cognitive_memory locomo-eval --manifest tests/fixtures/locomo/manifest.json
```

If the dataset file is missing, the command exits with setup instructions. It
does not download data automatically.

## Stage Diagnostics

`--stage-report` adds a CML-only diagnostic harness that separates four failure
surfaces:

- extraction: whether stored facts, events or reflections have provenance
  overlapping the LoCoMo evidence dialog ids
- retrieval: whether evidence-linked memories were selected in the top-k
  memories returned to CML
- answer synthesis: whether selected memories can be converted into a direct
  deterministic relation/object answer
- abstention: whether answering or abstaining was appropriate given evidence
  coverage and confidence

The deterministic answer synthesizer is report-only. It does not replace the
normal CML answer, does not tune the LoCoMo score and does not use expected
answers to produce text. Expected answers and expected-abstain flags are used
only after synthesis to score the diagnostic field.

`--answer-mode diagnostic-synthesis` is an explicit CML-only diagnostic mode
that uses the same selected memories to return a direct answer when a generic
relation/object, location, person or temporal template is confident enough.
It remains off by default, preserves low-confidence abstention and does not use
QA answers or evidence ids to produce text. It exists to test whether selected
evidence is answerable without dumping unrelated memory claims.

LoCoMo evidence ids are evaluation labels. They may be used after ingestion and
retrieval inside diagnostics, but they must never influence extraction, memory
writes, retrieval ranking or answer selection. The only path that runs generic
conversation extraction is still `--extract`.

`--qa-evidence-in-window-only` is also evaluation-only. After sample and turn
limits are applied, it keeps only QA items whose evidence dialog ids are fully
inside the selected episode window. This makes bounded subset runs honest about
whether the relevant evidence was even ingested. The filter is reported in the
output and does not change extraction, retrieval ranking, memory writes or
answer selection.

## Retrieval Modes

`--retrieval-mode governed` is the default. It uses the primary policy-aware
retrieval planner used by the rest of the prototype.

`--retrieval-mode hybrid` is only for LoCoMo/open conversational QA diagnostics.
It ranks stored memories with generic lexical overlap, speaker/entity overlap,
temporal cue matching, relation cue matching, memory-type weighting,
source-time/session diversity and conservative confidence gating. It still does
not use QA answers, LoCoMo evidence ids, memory provenance ids, image fields or
LLM calls for retrieval.

The hybrid mode is not the enterprise/governed memory path. It exists to study
raw conversational QA retrieval failures without weakening the governance
benchmark or changing production-like retrieval behavior.

## Extraction Modes

`--extractor rule-based` is the default when `--extract` is enabled. It runs the
deterministic generic conversation extractor and makes no external calls.

`--extractor llm` is optional and experimental. It uses a schema-constrained
open-conversation extractor backed by an injected provider or by the OpenAI
Responses API when `OPENAI_API_KEY` and `OPENAI_LLM_EXTRACTOR_MODEL` are set.
`OPENAI_MODEL` is accepted as a fallback model variable. If credentials or model
configuration are missing for a live run, setup fails clearly. The command does
not fake live results.

Live LLM extraction uses a local source-turn cache at:

```text
.cache/engram/llm_extract/
```

The cache key includes model, prompt/schema version, speaker, session id, dialog
id and a SHA-256 hash of the turn text. Cache entries may contain extracted
claims or support snippets from external dataset text, so `.cache/` is ignored
and must not be committed. API keys are never stored in cache entries.

Bounded-run controls:

- `--max-samples N`: alias for `--limit-samples`; do not pass both.
- `--sample-ids id1,id2`: evaluate only named LoCoMo samples.
- `--max-turns N`: ingest only the first N turns after sample filtering.
- `--max-api-calls N`: hard cap on uncached extraction calls; over-budget runs
  fail before the first API request.
- `--dry-run-cost-estimate`: report selected turns, planned calls and cache
  hits/misses without requiring an API key or making provider calls.
- `--llm-cache-dir PATH`: override the ignored local cache directory.
- `--qa-evidence-in-window-only`: score only QA items whose evidence turns are
  inside the selected turn window; diagnostics/evaluation only.
- `--answer-mode diagnostic-synthesis`: answer from selected memories only in
  the diagnostic LoCoMo CML path; default remains `normal`.

Unbounded real LoCoMo LLM extraction is intentionally blocked when it would
make more than a small uncached run. Use subset flags and an API-call cap first.

The LLM provider receives only:

- `dia_id`
- `speaker`
- `session_id`
- `timestamp`
- `text`

It does not receive LoCoMo QA questions, QA answers or `qa.evidence` labels.
It does not fetch images. It does not replace governed retrieval globally.

The output schema requires provenance, speaker, session, confidence, temporal
hints, entity mentions, relation type, memory type, source support and
reference-resolution status. Supported relation classes are:

- `person_attribute`
- `preference`
- `relationship`
- `event`
- `plan`
- `location`
- `object_location`
- `temporal_change`
- `commitment`
- `question_answerable_fact`
- `uncertainty`
- `ambiguous_reference`

Local validation rejects outputs with missing or wrong `dia_id`, unsupported
supporting text, low confidence, hallucinated entities, ambiguous or unresolved
references and sensitive content without explicit consent. Accepted candidates
still pass through the normal `MemoryController` and policy gate before any
fact is stored.

Extractor diagnostics report:

- `rule_based_evidence_memory_recall`
- `llm_evidence_memory_recall`
- `extraction_precision_sample`
- `hallucination_rejection_rate`
- `unsupported_memory_rejection_rate`
- `top_k_evidence_hit_rate`
- `unsafe_answer_rate`
- final local QA accuracy
- LLM cache/cost fields: selected samples, selected turns, planned calls, calls
  made, cache hits, cache misses, rejected LLM outputs and accepted memories

These metrics are diagnostics, not an official LoCoMo score.

Example local manifest for a manually downloaded dataset:

```json
{
  "datasets": [
    {
      "dataset_name": "locomo10_local",
      "source": "https://github.com/snap-research/locomo",
      "license": "CC BY-NC 4.0; reviewed locally before use",
      "language": "en",
      "domain": "generic",
      "real_vs_synthetic": "synthetic",
      "pii_status": "synthetic",
      "local_path": "locomo10.json",
      "approved_for_eval": true,
      "expected_schema": "locomo_json",
      "loader": "locomo_json",
      "notes": "Keep this manifest and locomo10.json under ignored data/external/locomo/."
    }
  ]
}
```

## How LoCoMo Differs From Engram's Governance Benchmark

LoCoMo is useful because it tests long-term multi-session conversational memory
outside Engram's hand-authored governance suites. It can expose extraction and
retrieval failures that synthetic structured/recruiting scenarios hide.

LoCoMo does not directly test:

- deletion compliance
- do-not-use constraints
- consent requirements
- GDPR/AI-Act governance behavior
- candidate/client/role scope isolation
- production persistence or audit workflows

Therefore LoCoMo should complement, not replace, the synthetic governance,
transcript and invariant suites.

## Current Status

- Loader/evaluator: ready for local text-only QA.
- Generic extraction mode: available behind `--extract`; deterministic,
  rule-based and early. It extracts conservative speaker-grounded conversation
  facts, named third-party facts and simple temporal/relationship/career
  statements plus generic location, origin, stay-location and event-summary
  patterns. It uses only dialogue turns, not QA answers or evidence ids, and is
  not an LLM extractor.
- Diagnostics mode: available behind `--diagnostics`; reports extraction counts,
  accepted facts/events, retrieval attempts, abstentions and relation/type
  summaries.
- Stage diagnostics mode: available behind `--stage-report`; reports CML-only
  extraction, retrieval, deterministic answer-synthesis and abstention
  diagnostics using evidence ids only after ingestion and retrieval. Stage
  summaries include failure-label rates such as missing extraction, retrieval
  miss, low-confidence abstention, unsafe answer without evidence and
  diagnostics-only cue rates for missing extraction with event, temporal,
  location, place-reference, event-summary, participant or identity signals.
  Stage summaries also report sample evidence-memory precision so extraction
  changes are judged by precision and recall, not recall alone.
- Evidence-window QA filtering: available behind
  `--qa-evidence-in-window-only`; it is used only to construct bounded
  evaluation subsets after turn limits, so small LLM runs do not score
  questions whose evidence was never ingested.
- Diagnostic answer mode: available behind `--answer-mode
  diagnostic-synthesis`; it tests direct answer construction from selected
  memories and remains separate from normal CML scoring.
- Hybrid retrieval mode: available behind `--retrieval-mode hybrid`; improves
  retrieval diagnostics on local LoCoMo but remains research-only and is not a
  production claim.
- Optional LLM extraction mode: available behind `--extract --extractor llm`;
  disabled by default, schema-constrained, label-isolated, cached and bounded.
  A live fake-fixture smoke run validates the API path, but no full real LoCoMo
  LLM score is claimed.
- Negative controls: extractor tests include independent non-LoCoMo fake
  conversations, paraphrased location/event variants and adversarial location
  sentences that must not be stored as location facts.
- Fake fixture: committed for tests only.
- Real LoCoMo dataset: kept under ignored `data/external/locomo/` and not
  committed.
- Real LoCoMo result: raw, governed `--extract` and hybrid diagnostic runs are
  documented in `docs/locomo_results.md`.
- Production claim: unsupported.
