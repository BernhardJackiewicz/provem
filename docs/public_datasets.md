# Public Dataset Candidate Registry

This registry tracks possible external validation sources. Entries here are
candidates only. A dataset is not approved until license, privacy and local
handling have been reviewed.

| Candidate | Source URL Placeholder | License | Language | Domain | Real vs Synthetic | PII / Anonymization Status | Memory-Governance Usefulness | Risks | Approved for Use |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Public call-center transcript dataset | TODO | TODO | English / varies | customer service | likely real or semi-real | unknown until reviewed | Tests messy calls, escalation, repeat callers and action safety | license ambiguity, latent PII, diarization noise | No |
| Customer-service transcript dataset | TODO | TODO | English / varies | customer service | varies | unknown until reviewed | Tests issue history, complaint status and follow-up actions | may be single-session QA rather than memory continuity | No |
| Dialog dataset with multi-turn conversations | TODO | TODO | English / multilingual | generic dialog | often synthetic or crowd-written | usually low PII but must review | Tests external phrasing and abstention outside hand-authored fixtures | may not include durable memory labels | No |
| Recruiting transcript sample from public source | TODO | TODO | English / German | recruiting | unknown | unknown until reviewed | Best domain fit for candidate/client scope, pitch safety and confidentiality | likely scarce, high PII risk | No |
| Anonymized internal recruiting transcripts | local only, not committed | internal review required | English / German | recruiting | real anonymized | must be anonymized before use | Strongest validation for MVP domain claims | highest privacy and consent risk | No |
| Synthetic but human-written transcripts | local only or future repo fixtures | project-owned | English / German | recruiting / customer service | synthetic | fake/no PII by design | Good bridge between hand-authored benchmark strings and real data | can still be over-shaped if authors know the extractor | No |

## Approval Requirements

Before a candidate can be used in `external-eval`:

- License metadata must be recorded.
- PII status must be reviewed and set to a safe value.
- Data must be local and outside git unless it is fake fixture data.
- A validation manifest must set `approved_for_eval: true`.
- Any labels must be documented as human-authored, synthetic or derived.
- Results must be reported as dataset-specific, not generalized to production.
