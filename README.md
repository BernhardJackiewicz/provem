# Engram

**A Hippocampal Memory Layer for AI**

A dependency-light research prototype for testing whether governed long-term
memory improves LLM-agent behavior over flat retrieval and long-context style
baselines.

The prototype implements the core hypothesis:

> Episodic Log = source of truth. Graph = temporal interpretation. Reflection =
> evidence-backed hypothesis. Policy Store = consent and use rules. Controller =
> only durable write authority.

Engram uses "hippocampal" as a functional analogy: episodic encoding,
temporal context, consolidation, retrieval gating and forgetting. It does not
claim biological fidelity.

This is not a production Graphiti/Letta/Mem0 deployment. It is a falsifiable
local harness that mirrors those roles with in-memory ports so benchmark
failure modes can be exercised before integrating heavy services.

## Honest MVP Status

- MVP 0: complete for this repo. The benchmark runner, deterministic synthetic
  dataset, baselines and metrics are runnable.
- MVP 1: close to complete as a local research prototype. It has a
  non-autonomous memory core with governance, temporal facts, provenance,
  deletion and scoped retrieval, but no production persistence or external graph
  engine.
- MVP 1 benchmark hardening: a second deterministic noisy natural-language
  suite now exists. It is intentionally harder than the structured benchmark.
  Earlier unresolved-reference failures are tracked in
  `docs/failure_taxonomy.md`; the current fixes are synthetic safety fixes, not
  proof of general coreference handling.
- MVP 1 domain prototype: a deterministic recruiting benchmark now exists for
  candidate/client memory, pitch safety, confidentiality and recruiting-specific
  governance. It is synthetic domain evidence, not production proof.
- MVP 1 adversarial evaluation: a 75-scenario adversarial suite now exists,
  including deterministic mutations. It is designed to find failures after the
  structured/noisy/recruiting suites became too easy.
- MVP 1 adversarial hardening: source metadata, source-conflict abstention,
  prompt-injection/sensitive-content quarantine, mixed-scope identity checks
  and out-of-order event handling now exist. These are local safety controls,
  not production trust or entity-resolution infrastructure.
- MVP 1 event-model groundwork: a local `MemoryEvent`/participant/relation
  layer now links some recruiting facts to candidate/client/role context. This
  is conceptual groundwork for a future temporal graph backend, not Graphiti.
- MVP 1 freeze candidate: architecture and quality-gate docs now define the
  current local/synthetic bar. This is a research freeze candidate, not a
  production readiness milestone.
- MVP 1.5 local durability groundwork: an explicit JSONL snapshot path now
  persists episodes, candidates, temporal facts, memory events, reflections,
  policy flags and optional retrieval traces. This is local research
  persistence only, not a production database or privacy workflow.
- MVP 1.6 transcript evaluation groundwork: a local transcript evaluation
  harness now loads fake/anonymized JSON or JSONL call transcripts, converts
  turns to episodes, scores optional human labels and reports redacted
  diagnostics. It is not production transcript processing or real-world proof.
- MVP 1.7 core reliability groundwork: invariant tests, seeded fuzz/replay
  checks, snapshot schema-version validation and a local quality-gate command
  now guard the memory core against unsafe regressions.
- MVP 1.8 external validation readiness: a manifest-gated local external
  transcript evaluator now maps approved JSON/JSONL datasets into the existing
  transcript schema. It does not download data, include real PII or prove
  external performance.
- MVP 2.0 start: Mem0 is the first optional external baseline path. The default
  repo still runs without Mem0. `--include-mem0` skips clearly when Mem0 is not
  installed or configured, and `--strict-optional` fails clearly.
- MVP 2.1 start: Graphiti mapping and local parity scaffolding now exist. A
  dependency-free `LocalGraphitiParityBackend` exercises the Graphiti-like
  contract against the current local store and policy gate. This is not a live
  Graphiti integration.
- MVP 2: not implemented. There is no real Letta/MemFS stateful agent.
- MVP 2 preparation: adapter contracts, mocks, optional extras, and real
  integration stubs exist. Mem0 now has a guarded optional baseline adapter,
  but it is not part of default execution and has not been validated against a
  configured Mem0 service in this environment. Mem0 live comparison is deferred
  until a Python 3.10+ runtime and Mem0 Platform or OSS setup are available.
- MVP 3.0 local dry-run start: SleepCycle now proposes evidence-backed
  consolidation decisions and review items without applying durable writes.
  It is a local proposal/review queue, not autonomous truth creation.
- MVP 3.1 local consolidation evaluation: a deterministic harness now compares
  no consolidation, dry-run proposals and evaluation-only simulated approval on
  fake scenarios. It does not implement durable apply mode.
- MVP 3.2 scope-aware consolidation: reflections and consolidated memories now
  preserve candidate/client/role/project/user scope and explicit reflection
  types. This allows safe simulated approval for scoped memories in the
  evaluation copy only. Durable apply mode is still not implemented.
- MVP 3.3 human review queue: SleepCycle decisions can now be turned into
  auditable review items with risk classification and simulated reviewer
  decisions. Simulation is local and evaluation-only; it is not a production
  human-review or apply workflow.
- MVP 3.3 calibration: the local review simulation now demonstrates at least
  one safe low-risk approval while still rejecting high-risk items. Broad
  `user/default` sensitive items no longer block unrelated low-risk user
  preferences, but same-subject/same-relation and candidate/client/role
  conflicts remain conservative.

## What Is Implemented

- Episodic evidence log.
- Temporal facts with `valid_at`, `invalid_at`, supersession and provenance.
- Memory events with participants, relation types and event context for
  relationship-heavy recruiting cases.
- Memory candidates with importance, novelty, confidence, lifespan and risk.
- Policy store for deletion, do-not-use, sensitivity, consent and project scope.
- Unified policy/safety evaluation for facts, reflections and memory events so
  event retrieval cannot silently bypass deleted evidence, do-not-use terms,
  provenance, project/user scope or prompt-injection quarantine checks.
- Memory controller as the only durable write authority.
- Retrieval planner with selected and excluded memories, reasons, provenance,
  confidence, abstention recommendation and abstention reason.
- Lightweight scope model for candidate/client/role/project filtering before
  ranking.
- Lightweight source model for user, candidate, client, recruiter-note, tool,
  CRM and system-policy memory sources.
- Prompt-injection-like and hidden-sensitive memory content quarantine before
  durable fact writes.
- Source-conflict handling that preserves direct candidate/client statements
  over weak recruiter notes and abstains on unresolved tool/user conflicts.
- Conservative reference resolver for policy requests such as "that company",
  "that client" and "that number".
- Sleep/consolidation dry-run layer that proposes evidence-backed reflection,
  conflict, decay and no-op decisions without mutating durable memory by
  default. `--apply` is reserved and intentionally not implemented.
- Consolidation evaluation harness for checking whether SleepCycle proposals
  would help downstream retrieval under simulated approval without increasing
  unsafe consolidation, stale resurrection, policy leakage or scope leakage.
- Scope-aware reflection metadata and retrieval filtering so candidate
  preferences, client requirements, role requirements and project patterns do
  not collapse into global reflections during simulated consolidation.
- ReviewQueue models and CLI for inspecting consolidation decisions, risk
  levels, simulated approval/rejection counts and review reasons without
  applying memory changes.
- Review calibration metrics for low-risk approval, medium-risk review,
  high-risk rejection, useful review items and over-conservative rejection.
- Benchmark harness with baselines:
  - No memory
  - Long context style latest-match
  - Flat vector-style lexical retrieval
  - Hybrid lexical + temporal retrieval
  - Graph-like temporal baseline without governance
  - Engram / Cognitive Memory Layer
- Unit tests for update handling, deletion, do-not-use, project scope,
  sensitivity, low-confidence candidates, reflection evidence/counter-evidence,
  benchmark coverage and expected-output anti-cheat behavior.
- Local JSONL snapshot persistence for explicit import/export of research
  memory state, including policy flags and optional retrieval traces.
- Transcript evaluation harness for fake or anonymized call transcripts, with
  optional labels, redacted reporting, local persistence smoke support and
  transcript-specific metrics.
- External validation manifest runner for approved local JSON/JSONL transcript
  datasets. It reuses the transcript evaluator, refuses unapproved or
  under-documented datasets and keeps committed fixtures fake.
- Core invariant catalog and deterministic invariant/fuzz tests for deletion,
  do-not-use, source conflict, prompt-injection quarantine, scope isolation,
  supersession, provenance, policy gates, replay and persistence reload safety.
- `quality-gate` CLI command for local benchmark, transcript and persistence
  smoke checks.
- Adapter contracts for Graphiti-like, Mem0-like, and Letta-like systems, with
  local mocks, explicit Graphiti/Letta stubs, and a guarded optional Mem0
  backend.
- Local Graphiti-parity backend and mapping helpers for episodes, temporal
  facts, memory events, participants, relations, provenance, policy metadata
  and scope metadata. These are contract tests, not live Graphiti behavior.
- Mem0 baseline normalization for answer text, selected memories when exposed,
  provenance when exposed, derived abstention behavior and latency. Missing
  Mem0 fields are marked unavailable rather than treated as successful evidence.
- Optional schema-constrained LLM extractor scaffolding that validates candidate
  output locally and proposes `MemoryCandidate` objects. It is disabled by
  default and makes no live LLM calls.

## Quick Start

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m cognitive_memory benchmark
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite noisy
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite recruiting
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite adversarial
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0
PYTHONPATH=src python3 -m cognitive_memory demo
PYTHONPATH=src python3 -m cognitive_memory export-memory --path demo.memory.jsonl --demo
PYTHONPATH=src python3 -m cognitive_memory import-memory --path demo.memory.jsonl --query "current work mode"
PYTHONPATH=src python3 -m cognitive_memory transcript-eval --input tests/fixtures/transcripts
PYTHONPATH=src python3 -m cognitive_memory external-eval --manifest tests/fixtures/external/manifest.json
PYTHONPATH=src python3 -m cognitive_memory mem0-env-check
PYTHONPATH=src python3 -m cognitive_memory graphiti-env-check
PYTHONPATH=src python3 scripts/check_graphiti_env.py
PYTHONPATH=src python3 scripts/smoke_graphiti.py
PYTHONPATH=src python3 -m cognitive_memory sleep-cycle --demo
PYTHONPATH=src python3 -m cognitive_memory consolidation-eval
PYTHONPATH=src python3 -m cognitive_memory review-queue --demo
PYTHONPATH=src python3 -m cognitive_memory quality-gate
```

If the package is installed in editable mode:

```bash
python3 -m pip install -e .
cml benchmark
cml benchmark --suite all --include-mem0
cml demo
cml export-memory --path demo.memory.jsonl --demo
cml import-memory --path demo.memory.jsonl --query "current work mode"
cml transcript-eval --input tests/fixtures/transcripts
cml external-eval --manifest tests/fixtures/external/manifest.json
cml mem0-env-check
cml graphiti-env-check
cml sleep-cycle --demo
cml consolidation-eval
cml review-queue --demo
cml quality-gate
```

`*.memory.jsonl` files are ignored by git because local snapshots may contain
user or candidate memory. The JSONL format is intentionally inspectable and
dependency-free, but it is not encrypted, concurrent, migrated or production
safe.

Local transcript datasets are also ignored through `data/` and `transcripts/`.
Only fake fixtures under `tests/fixtures/transcripts/` and
`tests/fixtures/external/` should be committed.

## Structured Episode Markup

The prototype includes a deterministic extractor for reproducible evaluation.
Episodes can contain lines like:

```text
FACT user|work_mode|remote
FACT user|work_mode|hybrid
PREFERENCE contact_channel=email
SENSITIVE user|medical_condition|migraine
DELETE company_acme
```

Natural-language patterns are intentionally minimal. The point of this prototype
is memory governance and evaluation, not production extraction quality.

The noisy benchmark uses a separate conservative rule extractor for phrases
such as:

```text
Remote used to be a hard requirement for me, but honestly hybrid might be fine now if the offer is strong.
Don't make a whole personality trait out of this, but today I really hated sales calls.
Please don't bring up Globex again.
Forget the salary number I mentioned earlier; it was just a rough thought.
For the gymbuddy project I care about Flutter, but for psychotest24 I don't want to touch code myself.
```

Ambiguous references, sarcasm and non-commitment should often abstain. The
prototype has a narrow deterministic resolver for policy commands such as "that
company", but it is not general natural-language coreference.

## Research Use

Run the built-in benchmark and inspect per-scenario traces:

```bash
PYTHONPATH=src python3 -m cognitive_memory benchmark --json
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite structured
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite noisy
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite recruiting
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite adversarial
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all
```

The default benchmark remains the 34-scenario structured suite for backwards
compatibility. The noisy suite contains 40 synthetic multi-session scenarios
with indirect phrasing, distractors, corrections, sensitivity, deletion,
do-not-use, project switching, abstention and reflection traps.

The recruiting suite contains 72 synthetic multi-session scenarios covering
candidate salary and location updates, notice periods, relocation, client and
role requirements, pitch safety, do-not-contact, do-not-mention,
confidentiality, sensitive facts without consent, candidate/client separation,
project/client scoping, historical questions, abstention, anaphora traps and
relationship-heavy candidate/client/role context.

The adversarial suite contains 75 synthetic scenarios and deterministic
mutations covering identity collisions, overlapping candidate/client/company
names, stale fact resurrection, vague deletion, prompt-injection-like memory
content, source conflicts, ambiguous references, hidden sensitive information
project switching inside one paragraph and relationship-heavy recruiting
contexts. Mutations include renames, reordering, distractors, template
paraphrases, outdated conflicts and irrelevant sensitive facts. This suite is
expected to expose failures; a perfect score would be a warning that the suite
is not hard enough.

External validation readiness is separate from the synthetic benchmark. The
`external-eval` command accepts a local validation manifest, refuses unapproved
or missing-license datasets and maps approved JSON/JSONL records into the same
transcript schema used by `transcript-eval`:

```bash
PYTHONPATH=src python3 -m cognitive_memory external-eval --manifest tests/fixtures/external/manifest.json
```

The committed external fixtures are tiny fake data. Real public or anonymized
datasets must remain outside git, usually under ignored `data/` or
`transcripts/`, and must be reviewed for license and PII status before
`approved_for_eval` is set.

Mem0 can be run as an optional external baseline:

```bash
PYTHONPATH=src python3 -m cognitive_memory mem0-env-check
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0 --strict-optional
```

Without Mem0 installed and configured, the non-strict command skips
`mem0_external` and the strict command exits with a clear setup error. If Mem0
does run, it receives only the same synthetic benchmark episodes and requests
as other baselines. It never receives expected outputs and no real PII is used.

Two Mem0 execution modes are recognized:

- Platform mode uses `mem0.MemoryClient` and requires `MEM0_API_KEY`.
- OSS mode uses `mem0.Memory.from_config` and requires `MEM0_MODE=oss` plus
  `MEM0_OSS_CONFIG_PATH` pointing to a local Mem0 config. A no-secret OSS run
  still needs local providers, for example Ollama LLM and embedding models plus
  a configured vector store. Skipped or failed setup is not benchmark evidence.

Detailed setup is documented in `docs/mem0_live_setup.md`.

Graphiti readiness is currently mapping/parity only:

```bash
PYTHONPATH=src python3 -m cognitive_memory graphiti-env-check
PYTHONPATH=src python3 scripts/check_graphiti_env.py
PYTHONPATH=src python3 scripts/smoke_graphiti.py
```

If Graphiti or Neo4j configuration is unavailable, the command reports setup
failure without printing secret values. The default benchmark and quality gate
do not require Graphiti. Mapping notes live in `docs/graphiti_mapping.md`;
live setup instructions live in `docs/graphiti_live_setup.md`.

The structured suite contains 34 synthetic multi-session scenarios
covering current facts, historical facts, updated preferences, contradictions,
deletion, do-not-use, sensitive data without consent, cross-project isolation,
abstention and reflection hallucination traps.

The output includes:

- `current_fact_accuracy`
- `structured_accuracy`
- `noisy_accuracy`
- `recruiting_accuracy`
- `adversarial_accuracy`
- `mutation_stability`
- `pitch_safety_accuracy`
- `candidate_current_preference_accuracy`
- `client_requirement_accuracy`
- `historical_fact_accuracy`
- `obsolete_memory_usage_rate`
- `deleted_memory_leakage`
- `do_not_use_leakage`
- `confidentiality_leakage`
- `do_not_contact_leakage`
- `candidate_client_scope_contamination`
- `unsafe_recall_rate`
- `prompt_injection_memory_success_rate`
- `stale_fact_resurrection_rate`
- `ambiguous_reference_abstention_rate`
- `source_conflict_handling_accuracy`
- `cross_project_contamination`
- `abstention_accuracy`
- `provenance_coverage`
- `reflection_trap_failure_rate`
- `anaphora_failure_rate`
- `p50_retrieval_latency_ms`
- `p95_retrieval_latency_ms`

These scenarios are still synthetic. A high score is not product evidence, and
the noisy/recruiting/adversarial suites are still partially rule-shaped. Extend
`cognitive_memory.benchmark` with domain-specific data before drawing product
conclusions.

Failure analysis for the currently fixed noisy/recruiting failures lives in
`docs/failure_taxonomy.md`. Adversarial evaluation notes live in
`docs/adversarial_evaluation.md`. Event model notes live in
`docs/event_model.md`. The freeze-candidate architecture review and quality
gate live in `docs/architecture_review.md` and `docs/mvp1_quality_gate.md`.
A perfect score on the non-adversarial
synthetic suites should be read as a signal to add harder cases, not as
production validation.

Current local CML benchmark snapshot after the local event-model pass:

- structured: 34/34
- noisy: 40/40
- recruiting: 72/72
- adversarial: 70/75
- all suites: 216/221
- adversarial `unsafe_recall_rate`: 0.0000
- adversarial `prompt_injection_memory_success_rate`: 0.0000
- adversarial `source_conflict_handling_accuracy`: 1.0000
- adversarial `abstention_accuracy`: 1.0000

The remaining adversarial failures are mostly safe abstentions and extraction
misses. This is still a local synthetic MVP 1 prototype.

## Transcript Evaluation

Synthetic benchmarks are not enough. The transcript harness loads JSON/JSONL
call transcripts with participants, turns, caller identity, optional existing
context and optional expected labels. It converts turns to episodes and runs
the same controller, policy and retrieval path as the benchmark.

```bash
PYTHONPATH=src python3 -m cognitive_memory transcript-eval --input tests/fixtures/transcripts
PYTHONPATH=src python3 -m cognitive_memory transcript-eval --input path/to/local/anonymized/transcripts --persist-path tmp.memory.jsonl
```

The fixture set currently has 11 fake transcripts: 10 labeled and 1 unlabeled
diagnostic. It covers recruiting calls, client intake, follow-ups, repeat
customer service, complaint escalation, appointment rescheduling, handwerk
repair follow-up, ambiguous identity, do-not-use, sensitive facts and ASR-like
noise. The current run intentionally exposes a complaint-escalation extraction
miss; that is useful failure evidence.

Metrics include extraction/write precision and recall, sensitive storage
violations, do-not-use leakage, identity and scope accuracy, current/historical
truth accuracy, abstention accuracy, follow-up action safety, provenance
coverage and transcript-to-memory latency.

See `docs/transcript_evaluation.md` for the schema and fixture methodology.

## Quality Gate

MVP 1.7 adds a local quality gate:

```bash
PYTHONPATH=src python3 -m cognitive_memory quality-gate
```

It checks the full synthetic benchmark, transcript fixtures and a persistence
smoke test. It does not replace the full unittest command. Core invariants are
documented in `docs/core_invariants.md`.

## Architecture

```text
LLM / Agent Caller
      |
      v
Memory Controller  <---- deterministic / noisy / recruiting / adversarial / optional LLM extractor
      |
      +--> Episodic Log: raw evidence
      +--> Temporal Fact Store: interpreted facts, validity windows, provenance
      +--> Memory Event Store: participants, relations, candidate/client/role context
      +--> Policy Store: deletion, do-not-use, consent, sensitivity, scope
      +--> Reference Resolver: narrow, conservative "that X" policy resolution
      +--> JSONL Snapshots: explicit local save/load for research state
      |
      v
Retrieval Planner: scope-first filtering, policy-aware selection, exclusions,
                   provenance, abstention reason

SleepCycle / Consolidation:
  local MVP 3 dry-run proposal engine; produces reviewable decisions and audit
  records without autonomous truth creation.

Consolidation Evaluation:
  local MVP 3.1 harness; compares no consolidation, dry-run proposals and an
  evaluation-only simulated approval copy. It never enables real apply mode.
  MVP 3.2 adds scope-aware reflection metadata for candidate/client/role/project
  consolidation inside that evaluation copy.

Review Queue:
  local MVP 3.3 review gate; converts SleepCycle decisions into review items,
  risk labels and simulated reviewer decisions. It still does not apply durable
  memory. The demo includes one low-risk approval, one high-risk rejection and
  medium-risk deferred items.
```

Run the local SleepCycle demo:

```bash
PYTHONPATH=src python3 -m cognitive_memory sleep-cycle --demo
```

This prints candidate/decision/review counts from fake data only. It does not
apply consolidation decisions. `--apply` is reserved for a future audited
implementation and currently fails clearly.

Run the local review queue demo:

```bash
PYTHONPATH=src python3 -m cognitive_memory review-queue --demo
PYTHONPATH=src python3 -m cognitive_memory review-queue --demo --policy approve_low_risk_only
```

The review queue prints counts, risks, statuses and reasons. It does not print
raw sensitive values and does not write durable reflections.

## MVP 2 Adapter Preparation

The adapter package is available at `cognitive_memory.adapters`.

```text
Memory Controller
      |
      v
TemporalGraphBackend
      +-- LocalTemporalGraphBackend      default, dependency-free
      +-- LocalGraphitiParityBackend     local Graphiti contract parity
      +-- MockGraphitiBackend            test mock, not Graphiti
      +-- GraphitiBackend                stub, not implemented

ExternalMemoryBackend
      +-- MockMem0Backend                test mock, not Mem0
      +-- Mem0Backend                    optional Mem0 baseline adapter

StatefulAgentBackend
      +-- MockLettaBackend               test mock, not Letta
      +-- LettaBackend                   stub, not implemented
```

Optional extras exist for future work:

```bash
python3 -m pip install -e ".[graphiti]"
python3 -m pip install -e ".[mem0]"
python3 -m pip install -e ".[letta]"
```

These extras only install candidate client packages. Graphiti and Letta still
raise clear stub errors. Mem0 can be run as an optional baseline when `mem0ai`
is installed and either platform or OSS Mem0 configuration is available.

Run the optional Mem0 baseline:

```bash
PYTHONPATH=src python3 -m cognitive_memory mem0-env-check
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0
```

If Mem0 is not installed or configured, this command skips `mem0_external` with
a clear message. To make missing optional setup fail the command:

```bash
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0 --strict-optional
```

Platform setup:

```bash
python3 -m pip install -e ".[mem0]"
export MEM0_API_KEY="..."
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0 --strict-optional
```

OSS/local setup:

```bash
python3 -m pip install -e ".[mem0]"
export MEM0_MODE=oss
export MEM0_OSS_CONFIG_PATH=/path/to/local/mem0_config.json
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0 --strict-optional
```

For OSS mode, the config must define usable local or self-hosted LLM, embedder
and vector-store components. The current repo does not commit such a config
because local model names, dimensions and vector-store paths are machine
specific.

Optional smoke test:

```bash
PYTHONPATH=src python3 scripts/smoke_mem0.py
```

Run the optional Graphiti setup check:

```bash
PYTHONPATH=src python3 -m cognitive_memory graphiti-env-check
PYTHONPATH=src python3 scripts/check_graphiti_env.py
PYTHONPATH=src python3 scripts/smoke_graphiti.py
```

The Graphiti path currently validates mapping and local parity only. A skipped
or failed setup check is not Graphiti benchmark evidence, and a passing setup
check would still only mean the environment appears ready for future adapter
work. `scripts/smoke_graphiti.py` is guarded: it exits clearly while
`GraphitiBackend` is still a stub and must not be reported as a live result.

## Known Limitations

- Extraction is deterministic and schema-driven; it does not test real LLM
  extraction errors.
- The noisy extractor is a conservative rule-based stress layer, not a general
  natural-language understanding system.
- The recruiting extractor and scenarios are deterministic and benchmark-shaped;
  they do not validate real recruiter conversations.
- Scope filtering relies on simple entity naming conventions and lightweight
  relation rules. It is a safety improvement, not a real ontology.
- The local event model is additive and deterministic. It helps with some
  relationship-heavy recruiting cases, but it is not a graph database, not
  robust coreference and not a production entity model.
- The reference resolver only handles simple, singular, same-scope antecedents.
  It abstains or ignores broad unresolved references instead of suppressing
  broad memory.
- The optional schema-constrained LLM extractor is only a local validation
  wrapper. It does not call an LLM unless a caller injects a provider.
- The noisy suite currently includes known failure probes, including ambiguous
  reference handling. The current probes pass after conservative fixes; do not
  treat that as real-world coverage.
- Runtime storage remains in memory by default. Explicit JSONL snapshots exist
  for local research save/load, but there is no Postgres, Neo4j, Graphiti,
  encryption, migration system or production persistence.
- Baselines are lightweight approximations, not full Mem0, Graphiti or
  production RAG systems.
- The optional Mem0 baseline maps synthetic benchmark episodes into Mem0
  messages; this is a first comparison path, not a tuned Mem0 evaluation.
- Mem0 live comparison is currently deferred in this environment because the
  configured Python/Mem0 runtime requirements are not met. Fake-client tests are
  not performance evidence.
- Adapter mocks are contract tests only; they are not performance or feature
  substitutes for the external projects.
- Graphiti and Letta stubs are intentionally unused by the default benchmark.
- The local Graphiti-parity backend validates mapping semantics against the
  in-memory store. It does not validate the Graphiti API, Neo4j, latency,
  scale, delete propagation or production graph operations.
- All benchmark suites are synthetic and can overfit implementation choices.
- Current synthetic benchmark accuracy is high enough that more adversarial
  failure cases are needed before making further research claims.
- The remaining adversarial failures are mostly safe abstentions or extraction
  misses around project switching inside one paragraph, renamed mutation cases
  and paraphrases such as "two weeks".
- The current high benchmark scores can still reflect benchmark shaping. They
  do not validate real transcripts, live integrations, production privacy
  controls or recruiting business outcomes.
- Transcript evaluation currently uses fake fixtures. Real or anonymized
  transcripts must stay local and must not be committed. Basic redaction in
  reports is not production anonymization.
- The quality gate is a local reliability check, not CI, deployment monitoring
  or production validation.
- JSONL `schema_version` checks are compatibility guards for research
  snapshots, not a migration framework.
- Latency numbers are local in-process timings, not service timings.
- MVP 3.0 SleepCycle is dry-run only. It can propose reflections, conflicts and
  decay metadata, but it does not automatically create durable truth or change
  retrieval ranking.
- MVP 3.1 simulated approval is a local evaluation device. It is not a human
  review product, not a safe apply workflow and not evidence that consolidation
  improves real-world agent behavior.
- MVP 3.2 scope-aware reflections are still local and synthetic. They reduce
  scoped leakage in the fake consolidation harness, but they do not validate
  real transcripts, human review, Graphiti persistence or production apply
  behavior.
- MVP 3.3 ReviewQueue is a local audit and simulation layer. It is not a human
  review UI, not a workflow system and not a durable apply path.
- MVP 3.3 calibration is still synthetic. It proves that the local gate can
  approve clearly low-risk fake proposals, not that real reviewers or live
  memory integrations are ready.

## Integration Path

- Replace the in-memory temporal fact store with Graphiti/Neo4j.
- Map local `MemoryEvent`, `EventParticipant` and `EventRelation` objects to a
  real temporal graph backend.
- Add real Graphiti service tests only after `docs/graphiti_mapping.md` parity
  expectations continue to pass locally.
- Compare the deterministic recruiting extractor with an optional
  schema-constrained LLM extractor on the same recruiting scenarios.
- Connect Letta/MemFS as the active state and procedural-memory layer.
- Run and calibrate the optional Mem0 baseline with a real configured Mem0
  service and noisier natural-language scenarios.
- Mem0 comparison is the next reasonable external baseline once review
  calibration stays green: unsafe approvals must remain zero, high-risk
  autoapproval must remain zero, review coverage must stay complete and the
  demo/evaluator must approve at least one useful low-risk item.
- Add harder scope and coreference cases before adding product features.
- Keep the controller as the single durable write authority.
- Keep `RetrievalResult.excluded_memories` and provenance traces; they are
  critical for debugging and research.
- Treat SleepCycle output as a review queue until a future apply path has its
  own controller/policy tests.
- Keep consolidation evaluation separate from production memory writes; the
  current harness can approve only inside an isolated evaluation copy.
- Preserve fine-grained reflection scope fields before any future apply path;
  otherwise consolidation can reintroduce candidate/client/role leakage.
- Keep ReviewQueue simulation separate from production approvals. High-risk
  items must never be auto-approved.
