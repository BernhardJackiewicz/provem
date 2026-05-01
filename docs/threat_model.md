# Memory Threat Model

## Primary Risks

- Stale memory use: the agent acts on invalidated facts.
- Deleted memory leakage: information remains available after a forget request.
- Deleted memory resurrection: local reload restores information that policy had
  already deleted or forbidden.
- Sensitive memory misuse: sensitive data is stored or retrieved without consent.
- Cross-project contamination: facts from one project affect another project.
- Reflection hallucination: consolidation creates unsupported user assumptions.
- Memory poisoning: malicious or low-quality content becomes durable memory.
- Prompt injection through memory: retrieved content changes tool or system
  behavior.
- Source conflict abuse: false corrections or low-trust sources overwrite
  verified candidate/client facts.
- Identity collision: similar candidate, client, role or company names cause
  wrong-subject recall.
- Stale fact resurrection: outdated facts reappear after newer contradictory
  facts, deletion or do-not-use requests.
- Over-personalization: the agent recalls true but contextually inappropriate
  information.
- Ambiguous natural-language commands: "that company" or similar references may
  be unresolved, over-broadly applied or ignored.
- Noisy extraction failure: indirect phrasing, sarcasm or casual sensitive
  disclosures may be missed or over-extracted.
- Recruiting harm: stale or forbidden candidate/client memory can lead to
  contacting a candidate who opted out, mentioning a forbidden company, exposing
  confidential compensation data or pitching a candidate to the wrong client.
- Candidate/client scope confusion: candidate preferences can be mistaken for
  client requirements, or client constraints can be attached to a candidate.
- Relationship-context confusion: candidate facts can be applied to the wrong
  client, role requirements can leak across roles, or company-as-client can be
  confused with company-as-employer.
- Relation confusion: same-subject but wrong-relation facts can be retrieved
  when the system should abstain.
- LLM extractor hallucination: a model-based extractor may output plausible
  but unsupported facts, inflated confidence or malformed structured data.
- LLM extractor prompt injection: source text may instruct the extractor to
  ignore policy, mark sensitive information as safe or write durable memory
  directly.
- Split-brain memory: multiple external systems disagree about what is current.
- Policy bypass: external memory returns facts the controller would forbid.
- Deletion mismatch: one backend forgets a fact while another still serves it.
- Stale external memory: external caches preserve invalidated facts.
- Local snapshot exposure: JSONL files may contain user/candidate memory if
  exported outside a controlled test environment.
- Snapshot compatibility drift: old or future JSONL records can be loaded
  incorrectly if schema assumptions are implicit.
- Core invariant regression: a local refactor can silently weaken deletion,
  do-not-use, provenance, scope or source-conflict behavior.
- Transcript dataset exposure: real call transcripts may contain phone
  numbers, emails, names, addresses, medical details, salary data or CRM notes.
- ASR and diarization errors: noisy transcripts can attribute statements to the
  wrong speaker or corrupt entity names.
- Consent missed in transcripts: sensitive facts mentioned casually can be
  over-stored if the transcript path bypasses consent policy.

## Prototype Controls

- Controller-only durable writes.
- Policy Store outside the fact graph.
- Provenance requirement for retrieval.
- Deletion and do-not-use filtering.
- Project and user scoping.
- Reflection requires multiple evidence points.
- Reflection records counter-evidence from invalidated facts.
- Retrieval traces include selected and excluded memories.
- Benchmark includes explicit leakage and reflection-trap scenarios.
- Noisy benchmark suite adds indirect phrasing, distractors, natural
  deletion/do-not-use requests, project switching, casual sensitive facts and
  reflection traps.
- Recruiting benchmark suite adds do-not-contact, do-not-mention,
  confidentiality, candidate/client separation, project scoping, pitch-safety
  and anaphora scenarios.
- Adversarial benchmark suite adds identity collisions, overlapping names,
  prompt-injection-like memory content, source conflicts, vague deletion,
  ambiguous references, hidden sensitive facts, stale fact resurrection and
  deterministic mutation variants.
- Lightweight source metadata distinguishes user statements, candidate
  statements, client statements, recruiter notes, tool records, CRM records and
  system policy constraints.
- Source conflict handling preserves direct candidate/client statements over
  weak recruiter notes and abstains when tool/user or verified-source conflicts
  are unresolved.
- Memory content with instruction-like text or hidden sensitive identifiers is
  quarantined before it can become a durable temporal fact.
- Out-of-order older facts are preserved as historical evidence instead of
  resurrecting stale current state.
- Lightweight scope metadata is attached to temporal facts and retrieval
  filters known wrong-scope candidate/client/role/project facts before ranking.
- A local event/relationship layer stores participants, relation types and
  candidate/client/role context for relationship-heavy recruiting cases.
- Event-aware retrieval only uses event context when it narrows ambiguity; if
  context is missing or conflicting, retrieval abstains.
- Retrieval returns explicit abstention reasons such as `insufficient_evidence`,
  `ambiguous_reference`, `wrong_scope`, `forbidden_memory` and
  `low_confidence`.
- Conservative reference resolution only applies "that company/client/number"
  policy requests when exactly one recent same-scope non-forbidden antecedent
  exists.
- Noisy rule extractor intentionally abstains on vague or non-committal
  statements rather than storing them as stable truths.
- Schema-constrained LLM extractor scaffolding validates JSON shape, allowed
  candidate types/actions, numeric fields, sensitivity metadata and confidence
  thresholds before proposals reach the controller.
- Extractors, including LLM-backed extractors, only propose memory candidates;
  the controller remains the only durable write authority.
- Architecture and quality-gate docs explicitly mark the current safety results
  as synthetic and local.
- Regression tests cover event retrieval under wrong-scope, deletion and
  do-not-use policy so relationship context cannot bypass policy silently.
- Facts, reflections and events share the same core policy exclusion path for
  deleted evidence, do-not-use terms, provenance, project/user mismatch and
  unsafe retrieved content.
- Local JSONL persistence tests verify that deleted, do-not-use, superseded,
  source-conflict and prompt-injection-quarantined memory does not become usable
  again after reload.
- `*.memory.jsonl` snapshots are ignored by git to reduce accidental commits of
  local memory exports.
- Snapshot records include `schema_version`; future versions fail clearly and
  old/minimal v1-shaped records import only when the type is known.
- Core invariant tests and seeded fuzz/replay tests guard the local safety
  contract.
- `quality-gate` runs benchmark, transcript fixture and persistence smoke
  checks as a local preflight.
- `data/` and `transcripts/` are ignored for local transcript datasets; only
  fake fixtures under `tests/fixtures/transcripts/` should be committed.
- Transcript evaluation withholds low-confidence caller content from durable
  extraction until identity is resolved.
- Transcript reports apply basic local redaction for emails, phone numbers and
  configured sensitive/redaction terms, but this is only a reporting helper.

## Controls Still Needed

- Real or anonymized recruiting transcripts to test whether the synthetic
  extractors and event model generalize, with strict local handling and no PII
  committed.
- Production-grade anonymization/redaction before any real transcript sharing.
- Diarization confidence handling and speaker-attribution checks beyond the
  current fixture metadata.
- Human review queue for high-impact reflections.
- Live LLM-based extraction experiments with deterministic schema validation,
  prompt-injection red-team cases and side-by-side comparison against the rule
  extractor.
- Stronger coreference/anaphora handling for natural deletion and do-not-use
  requests. The current resolver is intentionally narrow and deterministic.
- A real entity identity model; current scope isolation relies on naming
  conventions and relation keywords.
- A real temporal relationship graph. The local event model is Graphiti
  groundwork, not production graph storage or graph reasoning.
- Recruiting-specific human approval for high-impact actions such as pitch
  eligibility, do-not-contact changes and confidentiality overrides.
- Domain consent rules for candidate, client, role and project memory.
- Real recruiting workflow traces or stronger synthetic red-team cases before
  making recruiting product claims.
- Signed or hashed episode evidence.
- Audit UI for user-visible memory inspection.
- Separate red-team benchmark for poisoning and prompt injection.
- Stronger source-trust model with domain-specific confirmation workflows. The
  current source model is lightweight and deterministic, not a production trust
  system.
- Robust memory-injection red-team coverage beyond simple local pattern
  matching.
- Stronger mutation and paraphrase evaluation against non-handwritten traces.
- Adapter tests against real Graphiti, Letta and Mem0 deployments.
- Production persistence-layer deletion tests, encryption, migration strategy,
  access control and retention enforcement. Current JSONL snapshots only cover
  local research reload behavior.
- Real migration tooling if JSONL snapshots ever evolve beyond local research
  artifacts.
- CI integration for invariant and quality-gate commands.
- Domain-specific privacy and consent policies.
- Cross-backend reconciliation tests before enabling multiple memory systems at
  once.
