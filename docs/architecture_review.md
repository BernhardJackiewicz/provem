# Engram MVP 1 Architecture Review

This review documents the current local architecture before an MVP 1 freeze. It
is an engineering audit, not a production-readiness claim.

## Current Architecture

The repository implements Engram, a dependency-light, in-memory Cognitive
Memory Layer.
The important invariant is:

```text
Episode log = evidence
Temporal facts = interpreted current/historical claims
Memory events = local relationship context
Policy store = use restrictions
Controller = only durable write authority
Retrieval planner = policy-aware selection and abstention
JSONL snapshots = explicit local research persistence
Transcript evaluator = realistic local fixture harness
Invariant tests = core safety regression guard
```

The default system has no real Graphiti, Letta, Mem0, production database, UI
or live LLM integration. Adapter contracts and stubs exist for later MVP 2 work,
but the benchmark runs locally with standard Python.

## Data Flow

```text
Episode
  <- optional transcript turns from TranscriptEvaluation
  -> extractor proposes MemoryCandidate objects
  -> MemoryController evaluates policy and safety
  -> accepted candidates become TemporalFact objects
  -> accepted facts also create MemoryEvent objects when useful
  -> RetrievalPlanner filters by scope, centralized policy, source safety and time
  -> RetrievalResult returns selected memories, exclusions, provenance and abstention reason
  -> optional JSONL snapshot can persist local store, policy flags and retrieval traces
```

The benchmark systems receive the same scenario episodes and request. Expected
answers stay in evaluator data only; anti-cheat tests guard this.

The transcript evaluator receives JSON/JSONL files, converts turns and existing
context to episodes, and then uses the same controller/policy/retrieval path.
It has its own labels and diagnostics, but it does not become a second write
authority.

## Write Path

- `MemoryController.ingest_episode` stores the raw episode first.
- Extractors propose candidates; they do not write durable memory.
- Low-confidence, sensitive-without-consent and instruction-like candidates are
  ignored, require consent or are quarantined before fact creation.
- Deletion and do-not-use requests update `PolicyStore` and mark affected facts,
  but the raw evidence remains available for audit semantics.
- Newer same-source facts supersede older facts. Source conflicts can keep the
  existing direct statement, store a conflict or force retrieval abstention.
- Accepted facts create local `MemoryEvent` records for relationship-heavy
  recruiting context.

The controller must remain the only durable write authority. Future Graphiti,
Mem0 or Letta integrations should propose or store through this boundary, not
silently write policy-relevant truth elsewhere.

## Retrieval Path

- Query scope is inferred before ranking.
- Known wrong-scope facts are excluded before scoring can make them attractive.
- Policy exclusions run before selection: deleted evidence, do-not-use terms,
  sensitive policy, invalidation, project/user mismatch and provenance.
- Event-aware retrieval activates only for mixed candidate/client queries with
  enough explicit scope.
- Retrieval prefers abstention over unsafe recall for ambiguous identity,
  ambiguous reference, source conflict, forbidden memory and missing evidence.
- `RetrievalResult` carries selected memories, excluded memories with reasons,
  provenance, confidence, `abstain_recommended` and `abstain_reason`.

## Policy And Safety Path

The prototype has explicit synthetic controls for:

- deletion and do-not-use precedence
- sensitive data without consent
- prompt-injection-like memory content quarantine
- candidate/client/role/project scope isolation
- source trust and source conflict abstention
- stale fact ordering and historical queries
- conservative reference resolution for simple "that company/client/number"
  policy commands

Facts, reflections and memory events now share the same internal policy
evaluation path for deleted evidence, do-not-use, wrong user/project,
provenance and unsafe retrieved content. Retrieval still owns relation-specific
event matching, source-conflict checks across selected memories and abstention
decisions that require looking at a query result set.

These controls are local and deterministic. They do not replace a production
privacy model, source verification workflow, identity service or security
review.

## Core Reliability Path

MVP 1.7 adds an explicit invariant catalog and deterministic tests for the
core safety contract. The protected behaviors include deletion/do-not-use
precedence, prompt-injection quarantine, wrong-scope exclusion before ranking,
source-conflict abstention, supersession, provenance, exclusion reasons,
policy-gated events/reflections, persistence reload safety and replay
idempotency.

The seeded fuzz tests generate deterministic operation sequences and compare
semantic retrieval behavior instead of UUIDs. This is intended to catch unsafe
regressions without pretending to be exhaustive formal verification.

## Local Persistence Path

MVP 1.5 adds explicit JSONL snapshots through `cognitive_memory.persistence`
and CLI import/export helpers. A snapshot can contain episodes, memory
candidates, temporal facts, memory events, reflections, policy flags, store
audit records and optional retrieval traces.

`InMemoryStore` remains the default runtime store. JSONL snapshots are intended
for local inspection, reproducible debugging and reload tests. They are not a
production persistence layer: no encryption, locking, migrations,
authorization or concurrent write safety exists.

Snapshot records now carry `schema_version`. The importer accepts current v1
records and old/minimal v1-shaped records, but rejects unknown future versions
or unknown record types clearly.

## Transcript Evaluation Path

MVP 1.6 adds `cognitive_memory.transcript_eval` and the `transcript-eval` CLI
command. It loads fake or local anonymized transcripts with caller identity,
participants, turns, optional existing context and optional expected labels.

The evaluator:

- uses the same deterministic extractors and controller write path
- withholds low-confidence caller content from durable extraction
- scores labels for extraction/write recall, current and historical truth,
  identity/scope, abstention, leakage, follow-up safety and provenance
- emits redacted reports by default for names, emails, phone numbers and
  configured sensitive terms

This path is an evaluation harness, not production call processing. Real
transcripts must remain local and outside git.

## Event And Relationship Model

`MemoryEvent`, `EventParticipant`, `EventRelation` and `EventContext` add a
small relationship layer for recruiting cases. The current implementation can
represent:

- candidate preferences in a client context
- client or role requirements
- recruiter assumptions versus direct candidate/client statements
- candidate/client pitch status
- company-as-client versus company-as-employer distinctions
- objection raised/resolved timelines

This is Graphiti groundwork only. Event creation is still deterministic and
derived from accepted facts. It does not solve broad coreference, paragraph
splitting, entity linking or ontology design.

## Audit Findings

- No obvious dead top-level modules were found during this pass.
- Tests already cover most core safety paths; added freeze tests cover event
  retrieval under wrong-scope, deletion and do-not-use policy.
- The biggest quality risk is benchmark-shaped extraction. The noisy,
  recruiting and adversarial extractors contain curated phrase handling.
- Transcript fixtures expose the same risk: the complaint-escalation fixture is
  missed by the deterministic extractor.
- Event safety filtering previously duplicated some `PolicyStore` checks. That
  common policy path has now been unified for facts, reflections and events.
- Same-day candidate/client context linking is a useful local heuristic, not a
  robust conversation model.
- Source trust is deterministic metadata, not real source verification.
- Pitch-safety scenarios test governed retrieval and explicit status facts, not
  a full recruiting decision engine.
- CLI output and benchmark metrics are useful for local research, but latency
  numbers are in-process timings only.
- JSONL persistence is useful for local reload/regression checks, but it should
  not be used for real user/candidate memory without a separate privacy and
  security design.
- Transcript redaction is basic report masking and should not be confused with
  production anonymization.
- The quality-gate CLI is a local preflight check. It does not replace full
  unit tests, CI, production monitoring or external baseline validation.

## Do Not Change Casually

- Controller-only durable writes.
- Scope filtering before ranking.
- Deletion and do-not-use precedence over semantic relevance.
- Source conflict abstention where no clear precedence rule exists.
- Prompt-injection quarantine before durable fact writes.
- Provenance requirements for selected memory claims.
- Conservative abstention for ambiguous references and ambiguous identity.
- Local persistence reloads must not resurrect deleted, do-not-use,
  prompt-injection-quarantined or wrong-scope memory.
- Invariant tests should fail loudly rather than silently weakening safety for
  better recall.
- The distinction between adapter mocks/stubs and real integrations.

## MVP 2 Readiness

The architecture is ready for MVP 2 interface experiments, not production
integration. Before adding real Graphiti, Letta or Mem0 behavior, preserve these
contracts in tests:

- external systems cannot bypass the controller for durable policy-relevant
  writes
- external retrieval cannot return deleted, do-not-use or wrong-scope memory
  without being filtered
- adapter results must expose provenance and exclusion/abstention reasons
- local benchmark results must remain reproducible without optional services
