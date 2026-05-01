# Claim Register

Track every external or product-significant claim before using it in a paper,
deck or product narrative.

| Claim | Source | Evidence Level | Reproduced | Risk | Status |
| --- | --- | --- | --- | --- | --- |
| Temporal memory should reduce stale-memory use versus flat retrieval. | Local synthetic benchmark | Internal deterministic experiment | Yes, synthetic only | Medium | Internally supported, not product-proven |
| A policy store reduces deleted and do-not-use leakage versus graph-like temporal memory. | Local synthetic benchmark | Internal deterministic experiment | Yes, synthetic only | High | Internally supported, not product-proven |
| MVP 1 exists as a local synthetic research prototype. | Local code, tests, benchmark and docs | Internal deterministic experiment and architecture review | Yes, local only | Medium | Supported |
| MVP 1 is ready for production deployment. | None | Unsupported | No | High | Rejected |
| The MVP 1 freeze candidate establishes an audit boundary before MVP 2 work. | Local docs and verification commands | Documentation and tests | Yes, local only | Low | Supported as process claim |
| MVP 1.5 has local JSONL persistence for episodes, facts, events, policy flags and retrieval traces. | Local persistence code and tests | Code/tests | Yes, local only | Medium | Supported as local research persistence |
| MVP 1.5 provides production-grade persistence. | None | Unsupported | No | High | Rejected |
| Facts, reflections and events share a common policy/safety exclusion path. | Local policy/retrieval code and tests | Code/tests | Yes, local only | Medium | Supported as architecture cleanup |
| MVP 1.6 has a local transcript evaluation harness. | Local transcript evaluation code, fake fixtures and tests | Code/tests and local transcript-eval command | Yes, fake fixtures only | Medium | Supported as evaluation harness, not product evidence |
| Transcript evaluation validates real-world call performance. | None | Unsupported | No | High | Rejected |
| Basic transcript report redaction is production-grade anonymization. | None | Unsupported | No | High | Rejected |
| The first transcript fixture run exposes a complaint-escalation extraction miss. | Local fake transcript fixtures | Internal deterministic experiment | Yes, fake fixtures only | Medium | Supported as failure evidence |
| No real PII is committed in transcript fixtures. | Fixture inspection and fake test data | Code/data review | Yes | High | Supported for current fixtures only |
| MVP 1.7 has a documented core invariant catalog and deterministic invariant tests. | Local docs and tests | Code/tests | Yes, local only | Medium | Supported as local reliability guard |
| Seeded fuzz tests prove memory safety exhaustively. | None | Unsupported | No | High | Rejected; fuzzing is regression pressure, not proof |
| Quality-gate command validates benchmark, transcript fixtures and persistence smoke locally. | Local CLI command | Code/tests and command run | Yes, local only | Medium | Supported as local preflight check |
| Quality-gate command replaces full unit tests or CI. | None | Unsupported | No | High | Rejected |
| JSONL snapshot schema versioning is production migration support. | None | Unsupported | No | High | Rejected |
| MVP 1.8 has a manifest-gated external validation readiness path. | Local external-eval code, fake external fixtures and tests | Code/tests | Yes, fake fixtures only | Medium | Supported as readiness harness, not external validation evidence |
| External validation has been run on real public or anonymized datasets. | None | Unsupported | No | High | Rejected |
| The committed external fixtures prove real-world transcript performance. | None | Unsupported | No | High | Rejected |
| The external-eval command downloads or manages public datasets. | None | Unsupported | No | Medium | Rejected; datasets are local-only and manually reviewed |
| External dataset manifests enforce approval, license metadata and safe PII status before evaluation. | Local external-eval code and tests | Code/tests | Yes, local only | Medium | Supported as local guardrail |
| Basic external-eval redaction is production anonymization. | None | Unsupported | No | High | Rejected |
| The structured benchmark result proves robust real-world memory. | None | Unsupported | No | High | Rejected |
| A noisy natural-language benchmark suite exists. | Local benchmark code | Code/tests and local benchmark command | Yes, synthetic only | Medium | Supported as stress harness, not product evidence |
| The noisy suite exposed at least one CML limitation before the scope/reference hardening pass. | Local noisy benchmark and failure taxonomy | Internal deterministic experiment | Yes, synthetic only | Medium | Fixed on current synthetic suite; still not product evidence |
| The noisy extractor understands arbitrary natural language. | None | Unsupported | No | High | Rejected |
| A recruiting benchmark suite exists. | Local benchmark code | Code/tests and local benchmark command | Yes, synthetic only | Medium | Supported as domain stress harness, not product evidence |
| The recruiting benchmark proves production recruiter readiness. | None | Unsupported | No | High | Rejected |
| The recruiting suite exposed candidate/client scope and anaphora weaknesses before the scope/reference hardening pass. | Local recruiting benchmark and failure taxonomy | Internal deterministic experiment | Yes, synthetic only | Medium | Fixed on current synthetic suite; still useful as failure evidence |
| Engram / the Cognitive Memory Layer outperforms local baselines on the synthetic recruiting suite. | Local recruiting benchmark | Internal deterministic experiment | Yes, synthetic only | High | Internally supported, not domain-proven |
| The current synthetic benchmark score proves real-world scope or coreference safety. | None | Unsupported | No | High | Rejected |
| An adversarial benchmark suite exists. | Local benchmark code | Code/tests and local benchmark command | Yes, synthetic only | Medium | Supported as failure-discovery harness, not product evidence |
| The adversarial suite proves production robustness. | None | Unsupported | No | High | Rejected |
| The current adversarial results identify source-conflict, ambiguous-reference and unsafe-recall weaknesses. | Local adversarial benchmark | Internal deterministic experiment | Yes, synthetic only | Medium | Internally supported as failure evidence |
| The adversarial hardening pass materially reduced unsafe recall, prompt-injection memory success and source-conflict failures on the local synthetic adversarial suite. | Local adversarial benchmark | Internal deterministic experiment | Yes, synthetic only | High | Internally supported, not product-proven |
| The system has a production-grade source trust model. | None | Unsupported | No | High | Rejected |
| Local prompt-injection quarantine makes memory injection solved. | None | Unsupported | No | High | Rejected; pattern checks are partial controls |
| The system can safely resolve identity collisions in real recruiting data. | None | Unsupported | No | High | Rejected; current behavior often safe-abstains |
| Mutation stability on synthetic scenario variants predicts real-world paraphrase robustness. | None | Unsupported | No | High | Rejected |
| Lightweight scope filtering reduces candidate/client contamination on the synthetic recruiting suite. | Local recruiting benchmark | Internal deterministic experiment | Yes, synthetic only | Medium | Internally supported, not domain-proven |
| The deterministic reference resolver solves general anaphora/coreference. | None | Unsupported | No | High | Rejected |
| The deterministic reference resolver safely handles simple singular same-scope policy references in the synthetic suite. | Local noisy/recruiting benchmark | Internal deterministic experiment | Yes, synthetic only | Medium | Internally supported, not product-proven |
| The current system can safely decide real recruiting pitch eligibility. | None | Unsupported | No | High | Rejected |
| A local event/relationship model exists for recruiting memory. | Local model/store/controller/retrieval code | Code/tests and synthetic benchmark scenarios | Yes, local only | Medium | Supported as conceptual MVP 1 groundwork, not production graph |
| The event model fixes all identity and coreference failures. | None | Unsupported | No | High | Rejected |
| The event model is equivalent to Graphiti integration. | None | Unsupported | No | High | Rejected |
| Event-aware retrieval improves client-specific candidate fact handling on synthetic scenarios. | Local recruiting/adversarial benchmark | Internal deterministic experiment | Yes, synthetic only | Medium | Internally supported, not product-proven |
| Current safety behavior is validated for production recruiting use. | None | Unsupported | No | High | Rejected |
| Current safety behavior is covered by local synthetic tests and regression guards. | Local tests and benchmark metrics | Internal deterministic experiment | Yes, synthetic only | High | Internally supported, not product-proven |
| Safety behavior remains valid after local snapshot reload. | Local persistence tests | Internal deterministic experiment | Yes, synthetic only | High | Internally supported for JSONL snapshots only |
| A schema-constrained LLM extractor interface exists. | Local extractor code | Code/tests | Yes, no live model | Low | Supported as interface/scaffold only |
| A live LLM extractor has been validated on the benchmark. | None | Unsupported | No | High | Rejected |
| Schema validation makes LLM extraction safe. | None | Unsupported | No | High | Rejected; validation is necessary but insufficient |
| Mem0 may be more token-efficient than graph-heavy memory stacks. | Vendor/project materials and planned baseline | Independent reproduction required | No | High | Counterhypothesis |
| MVP 2.0 includes a practically runnable optional Mem0 baseline path. | Local Mem0 adapter, benchmark runner, fake-client tests and one live Platform run | Code/tests plus synthetic live benchmark | Yes, synthetic only | Medium | Supported as optional execution path and narrow live baseline, not product evidence |
| Mem0 OSS/local mode is wired as an optional execution mode. | `Mem0Backend` mode handling for `Memory.from_config` / config path | Code/tests with fake Memory class; no live Mem0 package in current environment | Yes, fake SDK shape only | Medium | Supported as setup path, not live benchmark evidence |
| A Mem0 live environment checker exists. | `mem0-env-check` CLI and `scripts/check_mem0_env.py` | Code/tests; no live Mem0 package required | Yes, local setup check only | Low | Supported as readiness tooling, not benchmark evidence |
| Mem0 normalized benchmark records expose selected memories/provenance/abstention availability where available. | Local benchmark normalization code and tests | Code/tests | Yes, fake client only | Medium | Supported as local normalization behavior |
| Mem0 was evaluated live on this repo's synthetic all-suite benchmark. | `benchmark --suite all --include-mem0 --strict-optional` from a temporary Python 3.11 Mem0 Platform environment | Internal synthetic benchmark | Yes, synthetic only | High | Supported as a preliminary benchmark result: Mem0 external `81/221`, CML `216/221`; fairness audit required before using as comparison evidence |
| Mem0 live comparison is currently blocked/deferred in this environment. | Superseded by the Python 3.11 temporary environment run; system Python 3.9.6 still cannot run Mem0 directly | Historical setup status | No | Medium | Superseded; default environment still lacks Mem0, but a live synthetic Platform run exists |
| CML outperforms live Mem0 on the current synthetic benchmark. | `docs/mem0_comparison.md` and the live strict optional benchmark report | Internal synthetic benchmark | Yes, synthetic only | High | Too broad; current evidence shows CML beats the generic Mem0 adapter on this harness, but adapter/query mismatch is material |
| Mem0 simple-memory performance requires sanity validation before governance claims. | `mem0-sanity --strict-optional` | Internal synthetic live audit | Yes, synthetic only | High | Supported; current audited result is Mem0 `6/12` simple and `8/34` structured governance |
| Audited post-settle Mem0 all-suite results exist. | Attempted strict optional all-suite rerun | Incomplete live run | No | High | Unsupported; rerun hit Mem0 Platform monthly search quota |
| Graphiti is suitable for temporal facts and provenance. | Project docs/papers and planned integration | Adapter evaluation required | No | Medium | Candidate |
| Graphiti adapter contract exists; real Graphiti integration is not implemented. | Local adapter package | Code/interface inspection | Yes | Low | Supported |
| A Graphiti mapping contract exists for episodes, temporal facts, memory events, participants, relations, provenance and policy metadata. | `docs/graphiti_mapping.md` and local mapping helpers | Docs/code/tests | Yes, local only | Medium | Supported as scaffold, not live integration evidence |
| A local Graphiti-parity backend exists. | `LocalGraphitiParityBackend` and parity tests | Code/tests using local store | Yes, local only | Medium | Supported as semantic parity harness |
| Graphiti environment readiness can be checked without printing secrets. | `graphiti-env-check` CLI and tests | Code/tests | Yes, local setup check only | Low | Supported as readiness tooling, not live validation |
| A reproducible Graphiti live setup guide exists. | `docs/graphiti_live_setup.md`, `scripts/check_graphiti_env.py`, `scripts/smoke_graphiti.py`, optional compose file | Docs/scripts/tests; no live adapter run | Yes, readiness only | Medium | Supported as setup readiness, not benchmark evidence |
| Mem0 adapter contract exists and guarded optional Mem0 baseline code is present. | Local adapter package | Code/interface inspection and fake-client tests | Yes, without live service | Medium | Supported as integration path, not benchmark evidence |
| Letta adapter contract exists; real Letta integration is not implemented. | Local adapter package | Code/interface inspection | Yes | Low | Supported |
| Graphiti integration works. | None | Unsupported | No | High | Rejected |
| Graphiti benchmark results exist for this repo. | None | Unsupported unless a real configured Graphiti run succeeds | No | High | Rejected |
| Mem0 live benchmark results exist for this repo. | Live strict optional Mem0 Platform benchmark and Mem0 sanity audit on synthetic scenarios | Internal synthetic benchmark | Yes, synthetic only | High | Supported narrowly; see `docs/mem0_comparison.md` |
| Letta integration works. | None | Unsupported | No | High | Rejected |
| MVP 3.0 has a local SleepCycle dry-run proposal engine. | Local consolidation models, `SleepCycle`, CLI and tests | Code/tests, fake demo data only | Yes, local only | Medium | Supported as proposal/review queue, not autonomous consolidation |
| SleepCycle automatically creates durable truth. | None | Unsupported | No | High | Rejected; `--apply` is reserved and not implemented |
| SleepCycle decay changes retrieval ranking. | None | Unsupported | No | Medium | Rejected; decay is metadata/report-only |
| MVP 3.1 has a local consolidation evaluation harness. | `consolidation-eval`, fake consolidation scenarios and tests | Code/tests, fake scenarios only | Yes, local only | Medium | Supported as evaluation harness, not production review |
| Simulated approval proves real-world consolidation usefulness. | None | Unsupported | No | High | Rejected; simulated approval is isolated and synthetic |
| Simulated approval is a durable apply workflow. | None | Unsupported | No | High | Rejected; it writes only to an evaluation copy |
| Current consolidation evaluation shows clean safety metrics on local fake scenarios. | Local `consolidation-eval` command | Internal deterministic experiment | Yes, synthetic only | Medium | Internally supported, not product evidence |
| MVP 3.2 has scope-aware reflection metadata for candidate/client/role/project/user consolidation. | Local models, retrieval filtering, SleepCycle proposals and tests | Code/tests, fake scenarios only | Yes, local only | Medium | Supported as local scaffold, not production consolidation |
| Scope-aware reflections prevent real-world candidate/client/role leakage. | None | Unsupported | No | High | Rejected; current support is synthetic/local only |
| Scoped consolidation has been validated on real transcripts or live graph storage. | None | Unsupported | No | High | Rejected |
| Scope-aware simulated approval is a real human-review apply workflow. | None | Unsupported | No | High | Rejected; it still writes only to an evaluation copy |
| MVP 3.3 has a local ReviewQueue for SleepCycle decisions. | Local review models, CLI and tests | Code/tests, fake data only | Yes, local only | Medium | Supported as audit/simulation layer, not production workflow |
| ReviewQueue simulation is a production human-review workflow. | None | Unsupported | No | High | Rejected |
| ReviewQueue implements durable apply mode. | None | Unsupported | No | High | Rejected |
| High-risk consolidation items are auto-approved. | None | Unsupported | No | High | Rejected; local simulation forbids high-risk autoapproval |
| Review-aware consolidation metrics are clean on the local fake suite. | Local `consolidation-eval` command | Internal deterministic experiment | Yes, synthetic only | Medium | Internally supported, not product evidence |
| ReviewQueue can approve clearly low-risk fake consolidation proposals while rejecting high-risk items. | Local `review-queue --demo` and `consolidation-eval` | Code/tests, fake data only | Yes, synthetic only | Medium | Supported as local calibration, not production review evidence |
| ReviewQueue calibration proves Mem0/Graphiti/Letta readiness on live data. | None | Unsupported | No | High | Rejected; it only supports moving to the next no-PII comparison step |
| Reflection can improve long-range personalization. | Generative-agents-style hypothesis | Needs ablation and hallucination metric | No | High | Hypothesis |
| The current implementation is MVP 1 complete in a production sense. | None | Unsupported | No | High | Rejected |

## Evidence Levels

- Peer-reviewed and independently reproduced.
- Public benchmark, independently reproduced.
- Public benchmark, self-reported.
- Vendor documentation.
- Internal experiment.
- Architectural hypothesis.

## Update Rule

No claim moves from `Hypothesis` to `Supported` until it has a reproducible
script, dataset description, metric definition and failure analysis.

Synthetic support is not enough for product claims. Any claim marked
`Internally supported` must be retested against noisier natural-language data and
at least one real external baseline before use in a strategy deck or paper.
