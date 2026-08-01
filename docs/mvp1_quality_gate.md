# Provem MVP 1 Quality Gate (developed under the working name Engram)

This checklist defines the bar for freezing MVP 1 as a local synthetic research
prototype. It is not a production release checklist.

## Benchmark Gate

The freeze candidate should meet or explicitly document any deviation from this
local snapshot:

| Suite | Required CML Result |
| --- | --- |
| structured | 34/34 |
| noisy | 40/40 |
| recruiting | 72/72 |
| adversarial | at least 70/75 |
| all | at least 216/221 |

Known adversarial misses are acceptable when they are safe abstentions,
extraction misses or documented benchmark mutation artifacts. Do not chase 100%
by adding string-specific rules.

The transcript harness is an MVP 1.6 readiness gate, not part of the synthetic
benchmark gate. The current fake fixture target is that `transcript-eval
--input tests/fixtures/transcripts` runs locally, reports 11 fake transcripts
and keeps leakage metrics at 0 while documenting any extraction misses.

MVP 1.7 adds a core reliability gate: invariant tests and seeded fuzz/replay
checks must pass before any MVP 2 adapter work starts.

MVP 1.8 adds an external validation readiness gate: `external-eval` must run on
the committed fake external manifest, while unapproved or missing-license
manifests must fail closed. This confirms the loader/manifest path only; it is
not evidence from real external datasets.

MVP 2.0 starts with an optional Mem0 baseline gate: default tests and
`quality-gate` must still run without Mem0, `--include-mem0` must skip missing
setup clearly, and `--include-mem0 --strict-optional` must fail clearly when
Mem0 is unavailable or unconfigured. A live Mem0 result is only valid when
either Mem0 Platform is configured with `MEM0_API_KEY` or Mem0 OSS is configured
with `MEM0_MODE=oss`, `MEM0_OSS_CONFIG_PATH`, and working local/self-hosted
providers. `mem0-env-check` is a setup preflight only; it is not benchmark
evidence. When Mem0 is configured, `mem0-sanity --strict-optional` must be run
before making governance comparisons; the current live audit shows partial
simple-memory retrieval and material adapter/query mismatch.

MVP 2.1 starts with an optional Graphiti parity gate: default tests and
`quality-gate` must still run without Graphiti. `LocalGraphitiParityBackend`
must preserve local semantic behavior for current/historical facts, event
relationships, policy metadata, source conflicts, scope filtering and
provenance. `graphiti-env-check` is a setup preflight only; it is not live
Graphiti validation. `scripts/check_graphiti_env.py` and
`scripts/smoke_graphiti.py` may fail with exit code `2` in the current
environment; that is acceptable when the output clearly reports missing
Graphiti setup or the unimplemented adapter.

MVP 3.0 starts with a local SleepCycle dry-run gate: `sleep-cycle --demo` must
produce consolidation candidates and reviewable decisions without durable
memory writes. `--apply` must fail clearly because autonomous apply behavior is
not implemented.

## Safety Metric Gate

For Engram / the Cognitive Memory Layer on the current synthetic benchmark:

| Metric | Required Status |
| --- | --- |
| deleted_memory_leakage | 0.0000 |
| do_not_use_leakage | 0.0000 |
| confidentiality_leakage | 0.0000 |
| do_not_contact_leakage | 0.0000 |
| candidate_client_scope_contamination | 0.0000 |
| unsafe_recall_rate | 0.0000 |
| prompt_injection_memory_success_rate | 0.0000 |
| source_conflict_handling_accuracy | 1.0000 |
| abstention_accuracy | 1.0000 |
| provenance_coverage | 1.0000 for non-abstained memory claims |

If a future change lowers raw accuracy but preserves safety by abstaining, it
can still be acceptable. If a future change improves raw accuracy by leaking
forbidden or wrong-scope memory, it fails the gate.

## Architecture Gate

Pass criteria:

- `MemoryController` remains the only durable write authority.
- Extractors and optional LLM scaffolds only propose `MemoryCandidate` objects.
- Retrieval filters known scope, policy and source-safety violations before
  ranking.
- Deleted and do-not-use terms block fact and event retrieval.
- Source conflicts abstain unless a clear local precedence rule exists.
- Event-aware retrieval remains conservative and query-scope gated.
- Facts, reflections and events share the same core policy exclusion path.
- JSONL reload preserves policy flags, source trust, invalidation/supersession
  and event context without resurrecting unsafe memory.
- Snapshot import validates schema versions and rejects unsupported future
  versions clearly.
- Replaying the same episode sequence does not create duplicate active truth.
- Seeded fuzz sequences preserve safety invariants and deterministic semantic
  retrieval behavior.
- Transcript-derived memories use the same controller, policy and retrieval
  path as benchmark episodes.
- Low-confidence transcript identity does not become durable extracted memory.
- External validation records are mapped into the existing transcript schema
  and cannot bypass transcript evaluation, controller policy or redacted
  reporting.
- External dataset manifests require approval, license metadata and a safe PII
  status before evaluation.
- Adapter mocks and stubs are documented as mocks/stubs.
- Default tests and benchmarks require no external services, API keys or heavy
  dependencies.
- Optional Mem0 baseline execution cannot read expected outputs and must mark
  unavailable Mem0 fields as unavailable instead of treating them as successful
  evidence.
- Mem0 Platform and Mem0 OSS setup failures must be reported as setup failures,
  not benchmark results.
- Graphiti parity tests must not bypass the controller or central policy gate.
- Graphiti setup failures must be reported as setup failures, not benchmark
  results.
- Graphiti smoke setup failures must not be converted into successful live
  results.
- SleepCycle dry-run decisions must not be described as applied memory.
- SleepCycle decay metadata must not change retrieval ranking without explicit
  tests.

Fail criteria:

- real Graphiti/Letta/Mem0 behavior is claimed without a live configured run
- external memory can bypass controller policy
- deleted, forbidden or wrong-scope memory can be selected because it scores
  highly
- benchmark expectations are read by any system under test
- docs imply production readiness or real-world recruiting validation
- local persistence is described as production durability, database migration
  support, encryption or privacy compliance
- invariant failures are hidden or converted into recall-focused benchmark
  tuning

## Documentation Gate

Required documents:

- `README.md`: honest MVP status and benchmark snapshot
- `docs/research_plan.md`: synthetic methodology and next validation steps
- `docs/claim_register.md`: supported, rejected and untested claims
- `docs/threat_model.md`: current controls and controls still needed
- `docs/failure_taxonomy.md`: fixed and remaining failure classes
- `docs/adversarial_evaluation.md`: adversarial result interpretation
- `docs/event_model.md`: local event model and Graphiti mapping notes
- `docs/graphiti_mapping.md`: explicit Graphiti mapping/parity contract
- `docs/graphiti_live_setup.md`: reproducible no-PII live setup instructions
- `docs/transcript_evaluation.md`: transcript schema, command and redaction
  limits
- `docs/external_validation.md`: manifest-gated external validation workflow
- `docs/public_datasets.md`: public/anonymized dataset candidate registry
- `docs/core_invariants.md`: non-negotiable memory-core invariants and test
  coverage
- `docs/architecture_review.md`: current architecture and fragile invariants
- `docs/mvp1_quality_gate.md`: this checklist

## Accepted MVP 1 Limitations

- All evidence is synthetic.
- Transcript fixtures are fake. They are more realistic than scenario strings
  but still not real-world validation.
- External validation readiness currently uses fake external fixtures only; no
  public or anonymized dataset result exists.
- Extractors are deterministic and benchmark-shaped.
- Recruiting usefulness is plausible but unvalidated on real transcripts.
- Project switching inside one paragraph is still brittle.
- General paraphrase and coreference handling are not solved.
- Source trust is local metadata, not a verification workflow.
- The event model is local Graphiti groundwork, not a temporal graph backend.
- Graphiti and Letta performance is untested.
- Mem0 fake-client tests exercise the optional path, and one live Mem0 Platform
  result exists for the synthetic all-suite benchmark. This does not validate
  real data or production behavior.
- Mem0 live comparison currently requires an explicit Python 3.10+ Mem0
  environment; the default system Python path remains dependency-free.
- Graphiti parity tests exercise local semantics only; no Graphiti/Neo4j
  service behavior is validated.
- Graphiti live smoke is readiness-only until a real adapter implementation
  writes and queries fake data successfully.
- Local JSONL snapshot persistence exists, but no production database,
  migrations, encryption, access control, UI or production privacy workflow
  exists.
