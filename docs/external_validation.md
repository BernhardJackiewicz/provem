# External Validation Readiness

MVP 1.8 prepares Engram to evaluate external, public or anonymized transcript
datasets safely. It does not download datasets and does not include real
transcripts.

## Why Synthetic Benchmarks Are Insufficient

The structured, noisy, recruiting and adversarial suites are useful regression
guards, but they are written around known failure classes. They cannot prove
that the deterministic extractors, scope rules, event model or policy gates
generalize to real call transcripts.

External validation should test whether the same memory governance behavior
holds under different phrasing, speaker behavior, domain assumptions, labels,
ASR noise, transcript length and entity ambiguity.

## What External Validation Should Prove

External validation should answer narrower questions:

- Can external records be mapped into the existing transcript schema without
  bypassing the controller?
- Do deletion, do-not-use, sensitive storage, identity and scope policies still
  hold on new data?
- Are extraction failures visible rather than hidden by synthetic fixtures?
- Do labeled current and historical truth checks match human annotations?
- Which domains or dataset styles break the local deterministic extractor?

It should not be used to claim production readiness until real anonymization,
review, persistence, access control and audit workflows exist.

## Acceptable Data Sources

Acceptable sources for local evaluation:

- Public transcript datasets with a reviewed license and no raw PII.
- Public dialog datasets that can be mapped into the transcript schema.
- Synthetic but human-written transcripts that were not authored to fit the
  extractor.
- Anonymized internal transcripts that have passed privacy review and are kept
  outside git.
- Small manually labeled local fixtures with fake data under `tests/fixtures`.

Every dataset must be recorded in a validation manifest and marked
`approved_for_eval: true` before `external-eval` will run.

## Unacceptable Data Handling

Do not:

- commit real transcripts, phone numbers, emails, names, addresses, salaries or
  CRM exports
- download datasets from the CLI
- evaluate data with unknown license metadata
- run data marked unapproved or with unsafe `pii_status`
- print raw sensitive values in reports
- treat local redaction as production anonymization
- claim real-world performance from fake fixtures

Local real/anonymized data belongs under ignored paths such as `data/` or
`transcripts/`, never in committed fixtures.

## Validation Manifest

The manifest can be a single dataset object, a list of objects, or an object
with a `datasets` list. Required fields:

- `dataset_name`
- `source`
- `license`
- `pii_status`
- `local_path`
- `approved_for_eval`
- `expected_schema`

Recommended fields:

- `language`
- `domain`
- `real_vs_synthetic`
- `notes`
- `loader`

Safe `pii_status` values are `fake`, `synthetic`, `anonymized`, `redacted`,
`deidentified` and `no_pii`. Anything else fails safely.

Example:

```json
{
  "dataset_name": "reviewed_local_fixture",
  "source": "internal anonymized sample",
  "license": "internal review 2026-04",
  "language": "en",
  "domain": "customer_service",
  "real_vs_synthetic": "real_anonymized",
  "pii_status": "anonymized",
  "local_path": "data/reviewed/customer_service.jsonl",
  "approved_for_eval": true,
  "expected_schema": "generic_transcript_jsonl",
  "notes": "Kept outside git."
}
```

## CLI

Run the committed fake fixture manifest:

```bash
PYTHONPATH=src python3 -m cognitive_memory external-eval --manifest tests/fixtures/external/manifest.json
```

The command refuses unapproved manifests and missing license metadata. It uses
the existing transcript evaluator and reports the same transcript metrics where
labels exist.

## Labeling Workflow

For each external dataset:

1. Review license and PII/anonymization status before adding the manifest.
2. Convert records into the existing transcript schema or add a small reviewed
   mapper.
3. Add human labels for current truth, historical truth, abstentions, scope,
   identity and forbidden outputs.
4. Run `external-eval` locally and inspect failures.
5. Update the claim register only with what the result actually supports.

## Limitations

- The committed external fixtures are fake and tiny.
- No public dataset has been downloaded or evaluated.
- `PublicDatasetLoaderStub` is only a placeholder for future dataset-specific
  mappers.
- The current loader only handles generic JSON/JSONL mapping into the existing
  transcript schema.
- Basic report redaction is not production anonymization.
- The deterministic extractor is still expected to fail on real transcripts.
