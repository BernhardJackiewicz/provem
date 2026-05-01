# Transcript Evaluation

The synthetic benchmark is useful for deterministic regression testing, but it
is not enough. Real call transcripts include messy phrasing, interruptions,
ASR-like mistakes, identity uncertainty, corrections, consent constraints and
multi-call continuity. The transcript evaluator is the MVP 1.6 bridge between
fully synthetic scenarios and later live integrations.

This is still local and deterministic. It makes no LLM calls, uses no external
services and should not be treated as production transcript processing.

## Input Schema

Transcript files can be JSON or JSONL. A directory input loads all `.json` and
`.jsonl` files below it.

Required or supported fields:

- `transcript_id`
- `domain`: `recruiting`, `customer_service`, `appointment`, `handwerk` or
  `generic`
- `timestamp`
- `caller_identity`: optional `phone_number_hash`, `stated_name`, `crm_id`,
  `confidence`
- `participants`: `speaker_id`, `role`, optional `name`
- `turns`: `speaker`, `text`, optional `timestamp`, optional `asr_confidence`
- `existing_context`: optional `prior_tickets`, `crm_facts`,
  `previous_call_summaries`
- `expected_labels`: optional human labels
- `redaction_terms`: optional fake or anonymized names/terms to mask in reports

Expected labels can include:

- `facts_that_should_be_stored`
- `facts_that_should_not_be_stored`
- `sensitive_facts`
- `consent_required_facts`
- `do_not_use_constraints`
- `do_not_contact_constraints`
- `current_truth`
- `historical_truth`
- `expected_abstentions`
- `expected_follow_up_actions`
- `forbidden_outputs`
- `identity_resolution_expected`
- `scope_expected`

Query-style labels use the same deterministic retrieval checks as the local
benchmark: `query`, optional `include`, optional `exclude`, optional
`expected_abstain`, optional `forbidden_outputs`, optional `time_scope` and
optional `as_of`.

## Commands

Run the fake fixture set:

```bash
PYTHONPATH=src python3 -m cognitive_memory transcript-eval --input tests/fixtures/transcripts
```

Run local anonymized transcripts and save one transcript memory snapshot:

```bash
PYTHONPATH=src python3 -m cognitive_memory transcript-eval \
  --input path/to/local/anonymized/transcripts \
  --persist-path tmp.memory.jsonl
```

Use `--json` for machine-readable output. Reports apply basic local redaction
for emails, phone numbers and configured `redaction_terms`. `--redact-salaries`
and `--redact-companies` add coarse masking. This is not production-grade
anonymization.

The transcript fixture command is also included in the MVP 1.7 local quality
gate:

```bash
PYTHONPATH=src python3 -m cognitive_memory quality-gate
```

The quality gate checks transcript leakage and minimum fixture recall, but it
does not replace the full unittest suite.

MVP 1.8 adds a separate manifest-gated external validation wrapper around this
same evaluator:

```bash
PYTHONPATH=src python3 -m cognitive_memory external-eval --manifest tests/fixtures/external/manifest.json
```

External validation does not define a second transcript schema. Approved
external JSON/JSONL records are mapped into the schema above, then evaluated
through the same controller, policy, retrieval and redacted reporting path.
The committed external fixtures are fake; real or anonymized datasets must stay
outside git and require license plus PII review before evaluation.

## Current Fixture Result

The first fixture run intentionally includes a failure:

- 11 fake transcripts
- 10 labeled transcripts
- 1 unlabeled diagnostic transcript
- complaint escalation is missed because the deterministic extractor does not
  understand "I want this escalated" as a durable `complaint_status` fact

Representative local output:

```text
transcripts: 11 labeled: 10
current_truth_accuracy: 0.9000
extraction_recall: 0.9000
memory_write_recall: 0.9000
sensitive_storage_violation_rate: 0.0000
do_not_use_leakage: 0.0000
```

The useful signal is the failure, not the score. It shows where synthetic
rules stop working.

## Safety Behavior

- Low-confidence caller identity causes transcript content to be withheld from
  durable extraction until identity is resolved.
- Sensitive-looking turns are marked high sensitivity and require explicit
  consent before durable storage.
- Do-not-use constraints from transcripts flow through the same controller and
  policy path as synthetic benchmark episodes.
- Raw real transcripts, phone numbers, emails, names, addresses or CRM records
  must not be committed.

## Limitations

- The fixtures are fake, not anonymized real data.
- The external validation fixtures are also fake; no public or internal real
  transcript dataset has been validated yet.
- The extractor is still deterministic and benchmark-shaped.
- ASR noise handling is minimal.
- Diarization errors are not modeled beyond speaker roles and confidence.
- Redaction is a reporting helper, not a privacy system.
- No Graphiti, Letta, Mem0 or production database behavior is validated here.
- Transcript outputs participate in local invariant thinking, but they are not
  a substitute for real anonymized transcript evaluation.
