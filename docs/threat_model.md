# Memory Threat Model

## Primary Risks

- Stale memory use: the agent acts on invalidated facts.
- Deleted memory leakage: information remains available after a forget request.
- Deleted memory resurrection: local reload restores information that policy had
  already deleted or forbidden.
- Sensitive memory misuse: sensitive data is stored or retrieved without consent.
- Cross-project contamination: facts from one project affect another project.
- Reflection hallucination: consolidation creates unsupported user assumptions.
- Over-consolidation: a sleep cycle turns weak, sensitive or conflicting
  evidence into stable memory without review.
- Cross-scope reflection leakage: a consolidated candidate/client/role/project
  hypothesis is used outside the scope that produced it.
- Review rubber-stamping: risky consolidation proposals are approved without
  enough evidence, scope confidence or policy review.
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
- External dataset misuse: public or local datasets may have incompatible
  licenses, unknown PII status or unreviewed annotations.
- Accidental dataset commit: reviewed local datasets may be placed under
  tracked paths instead of ignored `data/` or `transcripts/` paths.
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
- SleepCycle is dry-run by default and returns reviewable consolidation
  decisions instead of creating durable truth.
- SleepCycle `--apply` is reserved and fails clearly until a future audited
  apply path exists.
- Consolidation decisions preserve scope, evidence, counter-evidence,
  confidence, action, reason and review requirement.
- Consolidation evaluation rejects unsafe simulated approvals for
  review-required, stale/superseded, conflict-overlapping, forbidden,
  sensitive, prompt-injection-like or unresolved proposals.
- Simulated consolidation approval is confined to an evaluation copy and is not
  a live durable apply path.
- Scope-aware reflections preserve candidate, client, role, project, actor,
  subject, relation, confidence and reflection-type metadata.
- Reflection retrieval excludes known wrong-scope reflections before ranking.
- Consolidation evaluation reports scoped precision/recall and cross-scope,
  role-scope and candidate/client reflection leakage metrics.
- ReviewQueue converts consolidation decisions into review items with status,
  risk level, scope, evidence ids, counter-evidence ids and proposed action.
- Review simulation rejects or defers high-risk, unsupported, insufficient,
  stale, deleted, do-not-use, sensitive and prompt-injection-like items.
- High-risk review items are never auto-approved by local simulation.
- Review calibration allows unrelated low-risk user preferences to be approved
  in simulation even when a broad user-scope high-risk item also exists; the
  blocker still applies to concrete candidate/client/role and same-relation
  overlaps.
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
- `LocalGraphitiParityBackend` exercises the Graphiti-like mapping contract
  through the same local policy gate before real Graphiti work starts.
- `graphiti-env-check` reports Graphiti package and Neo4j config readiness
  without printing secret values; it is not live validation.
- `data/` and `transcripts/` are ignored for local transcript datasets; only
  fake fixtures under `tests/fixtures/transcripts/` should be committed.
- External validation manifests must mark datasets approved, include license
  metadata and use a safe PII status before evaluation runs.
- `external-eval` does not download datasets; it only evaluates reviewed local
  paths and maps them into the existing transcript schema.
- Fake external fixtures live under `tests/fixtures/external/`; real external
  datasets must stay outside git.
- Transcript evaluation withholds low-confidence caller content from durable
  extraction until identity is resolved.
- Transcript reports apply basic local redaction for emails, phone numbers and
  configured sensitive/redaction terms, but this is only a reporting helper.

## Controls Still Needed

- Real or anonymized recruiting transcripts to test whether the synthetic
  extractors and event model generalize, with strict local handling and no PII
  committed.
- Reviewed public dataset adapters after license and PII evaluation. The
  current public dataset loader is a stub, not an integration.
- Human labeling guidelines and reviewer checks for external validation
  datasets.
- Production-grade anonymization/redaction before any real transcript sharing.
- Diarization confidence handling and speaker-attribution checks beyond the
  current fixture metadata.
- Human review queue for high-impact reflections.
- Real review workflow for SleepCycle decisions before any durable apply path.
- Human review and explicit apply semantics for scope-aware reflections. The
  current scoped approval path is evaluation-only.
- Production review workflow, reviewer identity, authorization, audit UI and
  apply semantics. Current ReviewQueue is local simulation only.
- External-data validation for scoped reflections; current coverage is fake and
  deterministic.
- Tests for any future retrieval-ranking effect from decay metadata.
- Live LLM-based extraction experiments with deterministic schema validation,
  prompt-injection red-team cases and side-by-side comparison against the rule
  extractor.
- Stronger coreference/anaphora handling for natural deletion and do-not-use
  requests. The current resolver is intentionally narrow and deterministic.
- A real entity identity model; current scope isolation relies on naming
  conventions and relation keywords.
- A real temporal relationship graph. The local event model is Graphiti
  groundwork, not production graph storage or graph reasoning.
- Real Graphiti/Neo4j integration tests that prove local parity semantics
  survive service persistence, graph queries, deletion/do-not-use metadata and
  provenance mapping.
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
- Mem0 live comparison in a Python 3.10+ environment with Mem0 Platform or OSS
  runtime. Current Mem0 work is readiness only, not live benchmark evidence.
- Production persistence-layer deletion tests, encryption, migration strategy,
  access control and retention enforcement. Current JSONL snapshots only cover
  local research reload behavior.
- Real migration tooling if JSONL snapshots ever evolve beyond local research
  artifacts.
- CI integration for invariant and quality-gate commands.
- Domain-specific privacy and consent policies.
- Cross-backend reconciliation tests before enabling multiple memory systems at
  once.
