# Engram MVP 1 Quality Gate

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
- Adapter mocks and stubs are documented as mocks/stubs.
- Default tests and benchmarks require no external services, API keys or heavy
  dependencies.

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
- `docs/transcript_evaluation.md`: transcript schema, command and redaction
  limits
- `docs/core_invariants.md`: non-negotiable memory-core invariants and test
  coverage
- `docs/architecture_review.md`: current architecture and fragile invariants
- `docs/mvp1_quality_gate.md`: this checklist

## Accepted MVP 1 Limitations

- All evidence is synthetic.
- Transcript fixtures are fake. They are more realistic than scenario strings
  but still not real-world validation.
- Extractors are deterministic and benchmark-shaped.
- Recruiting usefulness is plausible but unvalidated on real transcripts.
- Project switching inside one paragraph is still brittle.
- General paraphrase and coreference handling are not solved.
- Source trust is local metadata, not a verification workflow.
- The event model is local Graphiti groundwork, not a temporal graph backend.
- Mem0, Graphiti and Letta performance is untested.
- Local JSONL snapshot persistence exists, but no production database,
  migrations, encryption, access control, UI or production privacy workflow
  exists.
