# Engram Research Plan

Engram is framed as **a hippocampal memory layer for AI**: a functional
analogy for episodic encoding, temporal context, controlled consolidation,
retrieval gating and forgetting. It does not claim biological fidelity.

## Hypothesis

An external memory layer with an episodic log, temporal facts, policy-aware
retrieval, controlled consolidation, provenance and selective forgetting
improves long-term agent behavior over flat vector retrieval and long-context
baselines, especially for updates, contradictions, deletion requests, sensitive
information and project separation.

The claim is functional, not biological. The system is inspired by memory
functions such as working memory, episodic evidence, semantic consolidation,
reconsolidation, decay and forgetting; it does not claim to model neural
biology.

## Research Questions

- RQ1: Does temporal memory reduce stale-memory use versus flat retrieval?
- RQ2: Does a controller plus policy store reduce invalid, deleted or forbidden
  memory leakage?
- RQ3: Does reflection improve long-range task success, or does it introduce
  unsupported user assumptions?
- RQ4: When is a hierarchical memory layer, such as Mem0, more efficient than a
  graph-heavy architecture?
- RQ5: Which retrieval signals provide the best accuracy, governance and latency
  tradeoff?

## Architecture Principle

- Episodic Log = source of truth.
- Temporal facts = interpretation with validity windows and provenance.
- Memory events = local relationship context for candidates, clients, roles and
  pitch/objection events.
- Reflection = evidence-backed hypothesis.
- Policy Store = consent, deletion, do-not-use, scope and sensitivity rules.
- Controller = only durable write authority.

## Evaluation Stages

1. MVP 0: Benchmark harness and baselines.
2. MVP 1: Non-autonomous memory core.
3. MVP 1.6: Transcript evaluation layer before live integrations.
4. MVP 1.7: Core reliability and invariant hardening.
5. MVP 1.8: External validation readiness for reviewed local datasets.
6. MVP 2.0: Optional Mem0 external baseline comparison on synthetic/fake data.
7. MVP 2.1: Graphiti mapping and backend parity scaffold.
8. MVP 2: Stateful agent integration through Letta/MemFS-style ports.
9. MVP 3.0: Local SleepCycle dry-run proposals, evidence checking, decay
   metadata and review queue.
10. MVP 4: Audit UI and domain pilot.

## Current Prototype Scope

The current code implements MVP 0 and a more serious local MVP 1 research
prototype. It includes a non-autonomous memory core, governed retrieval,
temporal invalidation, deletion, do-not-use, project scoping, provenance and a
34-scenario deterministic structured benchmark.

The benchmark has also been extended with a 40-scenario noisy
natural-language suite. This suite is still synthetic and deterministic, but it
tests indirect preferences, ambiguous statements, distractors, corrections,
project switches, natural deletion and do-not-use requests, casual sensitive
facts, abstention and reflection traps.

The current domain prototype adds a 72-scenario recruiting suite. It tests
candidate preferences, client and role requirements, pitch safety,
confidentiality, do-not-contact/do-not-mention requests, candidate/client scope
separation, historical-vs-current questions, abstention, anaphora traps and
relationship-heavy candidate/client/role context.
This is still synthetic domain evidence, not evidence from real recruiter
conversations.

The current foundation hardening pass adds a failure taxonomy, lightweight
scope metadata, scope-first retrieval filtering, explicit abstention reasons
and a conservative deterministic resolver for simple policy references like
"that company". These changes are safety-oriented and intentionally favor
abstention over unsafe recall.

The current adversarial pass adds a 75-scenario adversarial suite plus
deterministic mutations. It deliberately probes cases where the current system
should struggle: identity collisions, overlapping company/client/candidate
names, prompt-injection-like memory content, source conflicts, vague deletion,
ambiguous pronouns, hidden sensitive facts, stale fact resurrection and project
switching inside one paragraph and relationship-heavy recruiting context. This
suite is failure discovery, not a product scorecard.

The current adversarial hardening pass adds a lightweight source trust model,
prompt-injection/sensitive-content quarantine before durable writes, source
conflict abstention, mixed-scope identity checks, conservative ambiguous
reference handling and out-of-order event handling. These are general safety
controls. They intentionally increase safe abstention in some identity-collision
cases instead of trying to infer missing event structure.

The current event-model pass adds local `MemoryEvent`, `EventParticipant`,
`EventRelation` and `EventContext` models. Retrieval can use these events
conservatively for candidate/client/role-specific recruiting context, such as
client-specific salary expectations, pitch blocks and objection resolution. This
is not a live Graphiti integration and does not solve project-switch extraction
or general coreference.

The current freeze-candidate pass adds explicit architecture review and quality
gate documentation. It does not add product features. Its purpose is to make the
MVP 1 boundary auditable before MVP 2 adapter or live-integration work begins.

The current MVP 1.5 durability pass adds explicit local JSONL snapshots and a
shared policy/safety evaluation path for facts, reflections and memory events.
This makes local reload/regression testing possible without starting MVP 2. It
is not production persistence and does not change the synthetic nature of the
benchmark evidence.

The current MVP 1.6 transcript evaluation pass adds a local JSON/JSONL
transcript harness before live integrations. It converts transcript turns into
episodes, supports optional human labels, evaluates identity/scope/current and
historical truth checks, applies basic report redaction and preserves the same
controller/policy write path. The fixture set is fake and intentionally exposes
a complaint-escalation extraction miss.

The current MVP 1.7 core reliability pass adds an explicit invariant catalog,
deterministic invariant tests, seeded fuzz/replay checks, snapshot
schema-version validation and a local quality-gate command. It is a foundation
hardening pass, not a new feature or recall-improvement pass.

The current MVP 1.8 external validation readiness pass adds a manifest-gated
path for approved local JSON/JSONL transcript datasets. It maps external
records into the existing transcript-eval schema, refuses unapproved or
missing-license datasets and keeps public/anonymized data outside git. This is
readiness for external validation, not evidence from external datasets.

The current MVP 3.0 start adds a local SleepCycle dry-run proposal engine. It
returns `ConsolidationRun` records with candidates and decisions for repeated
evidence, conflicts, stale/superseded memories and review-required cases. It
does not automatically create durable reflections, rewrite facts, delete
memory or change retrieval ranking. `--apply` is reserved and intentionally not
implemented.

The current MVP 3.1 pass adds a consolidation evaluation harness. It compares
no consolidation, SleepCycle dry-run only and simulated human-approved
consolidation on fake local scenarios. Simulated approval writes only into an
isolated evaluation copy and rejects review-required, unsafe, stale,
conflicting, forbidden, sensitive or unrepresentable scoped decisions. This
measures whether proposals might help downstream retrieval without increasing
unsafe recall; it is not a production apply or human-review workflow.

An optional schema-constrained LLM extractor interface exists for future
comparison. It validates proposed `MemoryCandidate` output locally and keeps the
controller as the only durable write authority. It does not make live LLM calls
by default and has not been benchmarked against a real model.

The sleep cycle remains an MVP 3 stub. It is guarded by evidence requirements
and counter-evidence tracking, but it is not used as the main benchmarked memory
behavior.

External Graphiti and Letta integrations are not implemented. Mem0 now has a
guarded optional baseline adapter behind the `mem0` extra, but the current
environment does not have Mem0 installed or fully configured, so no live Mem0
result has been validated here.

The current MVP 2.0 start makes the optional Mem0 baseline path practically
runnable when `mem0ai` is installed and either Mem0 Platform or Mem0 OSS is
configured. Platform mode requires `MEM0_API_KEY`. OSS mode requires
`MEM0_MODE=oss`, `MEM0_OSS_CONFIG_PATH`, and working local/self-hosted LLM,
embedder and vector-store providers. The default benchmark still has no Mem0
dependency. Non-strict `--include-mem0` skips missing setup clearly;
`--strict-optional` fails clearly. Fake-client tests use existing benchmark
scenarios and verify result normalization without touching real PII or real
transcripts. `mem0-env-check` and `docs/mem0_live_setup.md` now document the
remaining setup path, but they do not create live benchmark evidence. In the
current environment the live Mem0 comparison is deferred because Python 3.10+
and a working Mem0 Platform or OSS runtime are not available.

The current MVP 2.1 start adds Graphiti mapping and local parity scaffolding.
`LocalGraphitiParityBackend` uses the existing local store and policy gate while
exposing Graphiti-like capability flags and method names. This lets tests prove
semantic parity for current truth, historical truth, supersession, policy
metadata, relationship events, scope filtering, source conflict abstention and
provenance before a real Graphiti/Neo4j adapter is attempted. `GraphitiBackend`
remains a lazy optional stub and no benchmark result uses live Graphiti.
`graphiti-env-check`, `scripts/check_graphiti_env.py` and
`scripts/smoke_graphiti.py` report package/config/adapter readiness only. The
optional `docker-compose.graphiti.yml` can start disposable local Neo4j, but it
is not used by default tests.

## Architecture Diagram

```text
User / Test Scenario
       |
       v
Episode ingestion
       ^
       |
Transcript evaluation harness
  - JSON/JSONL fake or anonymized transcripts
  - optional expected labels
  - redacted diagnostics
       ^
       |
External validation manifest
  - approved local JSON/JSONL datasets only
  - license and PII metadata gates
  - no downloads and no real data in repo
       |
       v
Memory Controller  (only durable write authority)
       |
       +-- Deterministic Extractor for structured scenarios
       +-- NoisyRuleBasedExtractor for noisy natural-language stress tests
       +-- RecruitingRuleBasedExtractor for synthetic recruiting scenarios
       +-- RecruitingRuleBasedExtractor for adversarial recruiting-like probes
       +-- Optional SchemaConstrainedLLMExtractor port for future extractor comparison
       +-- Episodic Log
       +-- Temporal Facts with valid_at / invalid_at / supersession
       +-- Memory Events with participants / relations / context
       +-- Policy Store with deletion / do-not-use / sensitivity / scope
       +-- Reference Resolver for narrow "that X" policy commands
       +-- Source Trust / Safety Layer for source conflicts and memory injection quarantine
       +-- JSONL Snapshot Persistence for explicit local save/load/debug traces
       |
       v
Retrieval Planner
       |
       +-- scope-first filtering before ranking
       +-- selected memories
       +-- excluded memories and reasons
       +-- provenance
       +-- confidence
       +-- abstention recommendation and reason

MVP 2 Adapter Preparation
       |
       +-- TemporalGraphBackend: LocalTemporalGraphBackend, LocalGraphitiParityBackend, MockGraphitiBackend, GraphitiBackend stub
       +-- ExternalMemoryBackend: MockMem0Backend, optional Mem0Backend baseline
       +-- StatefulAgentBackend: MockLettaBackend, LettaBackend stub

SleepCycle / Reflection Stub
       |
       +-- requires multiple evidence points by default
       +-- records counter-evidence from invalidated facts
       +-- consolidation-eval compares no/dry-run/simulated approval modes
       +-- not a full autonomous MVP 3 implementation
```

## Implemented Baselines

- No memory.
- Long context/latest-match.
- Flat lexical retrieval.
- Hybrid lexical + temporal retrieval.
- Graph-like temporal baseline without governance.
- Cognitive Memory Layer.

## Required Baselines Before Product Claims

- No memory.
- Long context/latest-match.
- Flat lexical or vector retrieval.
- Hybrid BM25 and vector retrieval.
- Mem0.
- Graphiti-only.
- Graphiti plus controller.
- Full Engram / Cognitive Memory Layer.

Mem0 is the first external comparison because it is the strongest practical
counterhypothesis: a token-efficient external memory layer may outperform a
more complex graph/controller stack in real deployments. Any Mem0 result must
state whether it came from a live configured Mem0 Platform run, a live
configured Mem0 OSS run, or the fake local test path.

At this checkpoint Mem0 live comparison is blocked/deferred, not completed. A
skipped `--include-mem0` run and fake-client tests are readiness evidence only.

## External Validation Methodology

External validation must use a manifest with explicit `dataset_name`, `source`,
`license`, `pii_status`, `local_path`, `approved_for_eval` and
`expected_schema`. The runner fails closed when approval, license or safe PII
status is missing. Public datasets must be downloaded and reviewed manually
outside the repo; the CLI never downloads data.

The first accepted data sources should be:

- fake external fixtures, to verify the manifest and loader path
- synthetic but human-written transcripts, to reduce extractor-shaped bias
- reviewed public transcript/dialog datasets, if license and PII status allow
- anonymized internal transcripts, only after privacy review and never
  committed

Any external result must be reported with dataset identity, labeling method and
known limitations. It must not be generalized to production without broader
validation.

## Benchmark Methodology

The default benchmark uses deterministic structured multi-session scenarios.
Every system receives the same episodes and retrieval request. Expected outputs
are used only by the evaluator, and tests check that the cognitive memory system
does not read hidden expected values.

Suite selection:

```bash
PYTHONPATH=src python3 -m cognitive_memory benchmark
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite structured
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite noisy
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite recruiting
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite adversarial
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all
```

Scenario families:

- Current fact retrieval.
- Historical fact retrieval with as-of dates.
- Updated preferences.
- Contradictions and supersession.
- Explicit deletion requests.
- Do-not-use constraints.
- Sensitive information without consent.
- Cross-project contamination.
- Abstention when memory is missing or forbidden.
- Reflection hallucination traps.

Noisy scenario families add:

- Indirect user preferences.
- Ambiguous or non-committal statements.
- Irrelevant distractors.
- Sarcasm and "do not overgeneralize" language.
- Natural-language corrections and updates.
- Natural deletion and do-not-use requests.
- Project-scoped facts expressed in one utterance.
- Sensitive facts mentioned casually without storage consent.
- Historical-vs-current questions using conversational wording.
- Known limitation probes, such as unresolved references like "that company".

Recruiting scenario families add:

- Candidate salary expectations changing over time.
- Remote, hybrid, on-site and relocation preferences changing over time.
- Notice period updates.
- Candidate do-not-contact and company do-not-mention requests.
- Client preferences and role requirements changing over time.
- Candidate objections, later resolution and competing offers.
- Confidentiality constraints.
- Sensitive facts mentioned casually without storage consent.
- Project/client scoping.
- Candidate-vs-client memory separation.
- Practical pitch-safety questions.
- Historical-vs-current recruiting questions.
- Abstention when a candidate/client fact is unknown or forbidden.
- Anaphora traps such as "that company" or "that client".

Adversarial scenario families add:

- Same candidate names across different clients/projects.
- Same company as both client and past employer.
- Overlapping candidate, client and role names.
- Role changes mid-conversation.
- Stale facts with newer contradictory facts.
- Vague deletion and do-not-use requests.
- Malicious or prompt-injection-like memory content.
- False corrections and tool/source conflicts.
- Ambiguous pronouns and multiple possible antecedents.
- Sensitive data hidden inside irrelevant notes.
- Project switching inside one paragraph.
- Recruiter assumptions versus candidate/client statements.
- Do-not-contact versus do-not-mention distinctions.
- Historical questions where old facts are correct.
- Current questions where old facts must not be used.

After adversarial hardening plus the local event-model pass, the local CML score
on the adversarial suite is 70/75. Safety metrics improved more than raw
accuracy:
`unsafe_recall_rate` 0.0000, `prompt_injection_memory_success_rate` 0.0000,
`ambiguous_reference_abstention_rate` 1.0000,
`source_conflict_handling_accuracy` 1.0000 and `abstention_accuracy` 1.0000.
This is still synthetic evidence only.

## Current Metrics

- Current Fact Accuracy.
- Historical Fact Accuracy.
- Obsolete Memory Usage Rate.
- Deleted Memory Leakage.
- Do-Not-Use Leakage.
- Cross-Project Contamination.
- Abstention Accuracy.
- Provenance Coverage.
- Abstention Reason.
- Structured Accuracy.
- Noisy Accuracy.
- Recruiting Accuracy.
- Adversarial Accuracy.
- Mutation Stability.
- Pitch Safety Accuracy.
- Candidate Current Preference Accuracy.
- Client Requirement Accuracy.
- Confidentiality Leakage.
- Do-Not-Contact Leakage.
- Candidate-Client Scope Contamination.
- Unsafe Recall Rate.
- Prompt Injection Memory Success Rate.
- Stale Fact Resurrection Rate.
- Ambiguous Reference Abstention Rate.
- Source Conflict Handling Accuracy.
- Anaphora Failure Rate.
- Reflection Trap Failure Rate.
- p50/p95 Retrieval Latency.

## Known Limitations

- Structured benchmark markup makes extraction easier than real conversations.
- The noisy suite is harder but still handcrafted and rule-shaped.
- The recruiting suite is domain-shaped but still synthetic. It is useful for
  exposing recruiting-specific failure modes, not for proving production
  recruiter performance.
- Current synthetic suites can all pass after the safety fixes. This should be
  treated as a benchmark-coverage warning, not as evidence of real-world
  superiority.
- The adversarial suite intentionally does not pass completely. Current
  failures are especially useful around project-switch extraction, mutation
  rename consistency and paraphrase extraction.
- The event model is local, deterministic and additive. It is useful as
  Graphiti groundwork, but it is not a real temporal knowledge graph.
- JSONL snapshots provide local reload/debug persistence only. They are not a
  production database, migration layer, encryption layer or privacy workflow.
- Snapshot schema-version checks are local compatibility guards, not production
  migration support.
- Invariant and fuzz tests make the memory core harder to break accidentally,
  but they are not exhaustive formal verification.
- Transcript evaluation currently uses fake fixtures. It is a realistic
  harness, not real-world evidence; local/anonymized transcripts must not be
  committed.
- Transcript report redaction is basic masking, not production anonymization.
- The transcript harness currently exposes a complaint-escalation extraction
  miss, showing that deterministic extraction does not generalize to all
  realistic utterances.
- Some pitch-safety scenarios use explicit synthetic status facts. They test
  governed retrieval and policy blocking, not a full recruiting decision engine.
- The current noisy extractor does not perform robust coreference resolution,
  paraphrase understanding or contradiction detection beyond curated patterns.
- The reference resolver only handles singular same-scope antecedents and
  rejects broad unresolved references. It is not general anaphora resolution.
- Scope filtering depends on deterministic entity names and relation keywords,
  not on a real identity graph or ontology.
- The recruiting extractor is deterministic and limited to narrow patterns; it
  will miss many realistic recruiter utterances.
- The optional schema-constrained LLM extractor validates output shape but does
  not guarantee factuality, good extraction, safe inference or resistance to
  prompt injection.
- The graph-like baseline is only an in-memory approximation, not Graphiti.
- Mem0 has an optional baseline path, but skipped runs are not evidence.
- Graphiti mapping/parity scaffolding exists, but `GraphitiBackend` and Letta
  adapter stubs are not real integrations.
- `LocalGraphitiParityBackend` proves local semantic compatibility only; it
  does not validate Graphiti API compatibility, Neo4j persistence, graph query
  correctness under load, or deletion propagation through a real service.
- Mock adapters validate contracts only; they do not validate external service
  behavior, latency, API compatibility or deletion semantics.
- Latency is local Python latency, not deployment latency.
- The cognitive layer currently passes all structured scenarios, so the
  structured result alone is not meaningful. The noisy suite is the first
  pressure test, but it is still not domain evidence.
- The reflection stub is intentionally conservative and not a full
  consolidation pipeline.
- The MVP 1 freeze candidate is a local research milestone. It is not a claim
  of production readiness, real-world recruiting usefulness or live integration
  performance.

## Next Steps

- Treat `docs/mvp1_quality_gate.md` as the entry checklist before any MVP 2
  work.
- Add anonymized or synthetic-realistic transcript packs and compare failures
  against the current fake fixture set before adding Graphiti/Letta/Mem0.
- Keep the MVP 1.7 invariants passing before any adapter or live extraction
  work.
- Compare the recruiting rule extractor with a schema-constrained LLM extractor
  on the same recruiting scenarios, with live model calls disabled by default
  and reported separately.
- Add harder recruiting cases from red-team scripts or anonymized real workflow
  traces before making domain claims.
- Expand the failure taxonomy with harder scope, relation and coreference
  failures before adding product features.
- Use adversarial and mutation failures to decide the next architecture-level
  fixes; do not patch individual strings to chase 100% adversarial accuracy.
- Revisit Mem0 baseline execution in a Python 3.10+ environment with either
  Mem0 Platform credentials or a complete Mem0 OSS stack; do not treat skipped
  setup as evidence.
- Keep Graphiti parity tests passing, then add real Graphiti/Neo4j service tests
  and compare against the graph-like baseline.
- Use `docs/graphiti_live_setup.md` to reproduce local setup before attempting
  any live Graphiti result; do not report smoke-test setup failures as evidence.
- Add Letta client integration only after controller write authority and policy
  boundaries are preserved in contract tests.
- Add persistent Postgres storage for episodes and audit logs.
- Start MVP 2 only after the MVP 1 benchmark includes domain data and at least
  one real external baseline.
