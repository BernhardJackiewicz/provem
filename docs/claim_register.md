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
| The structured benchmark result proves robust real-world memory. | None | Unsupported | No | High | Rejected |
| A noisy natural-language benchmark suite exists. | Local benchmark code | Code/tests and local benchmark command | Yes, synthetic only | Medium | Supported as stress harness, not product evidence |
| The noisy suite exposed at least one CML limitation before the scope/reference hardening pass. | Local noisy benchmark and failure taxonomy | Internal deterministic experiment | Yes, synthetic only | Medium | Fixed on current synthetic suite; still not product evidence |
| The noisy extractor understands arbitrary natural language. | None | Unsupported | No | High | Rejected |
| A recruiting benchmark suite exists. | Local benchmark code | Code/tests and local benchmark command | Yes, synthetic only | Medium | Supported as domain stress harness, not product evidence |
| The recruiting benchmark proves production recruiter readiness. | None | Unsupported | No | High | Rejected |
| The recruiting suite exposed candidate/client scope and anaphora weaknesses before the scope/reference hardening pass. | Local recruiting benchmark and failure taxonomy | Internal deterministic experiment | Yes, synthetic only | Medium | Fixed on current synthetic suite; still useful as failure evidence |
| The Cognitive Memory Layer outperforms local baselines on the synthetic recruiting suite. | Local recruiting benchmark | Internal deterministic experiment | Yes, synthetic only | High | Internally supported, not domain-proven |
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
| A schema-constrained LLM extractor interface exists. | Local extractor code | Code/tests | Yes, no live model | Low | Supported as interface/scaffold only |
| A live LLM extractor has been validated on the benchmark. | None | Unsupported | No | High | Rejected |
| Schema validation makes LLM extraction safe. | None | Unsupported | No | High | Rejected; validation is necessary but insufficient |
| Mem0 may be more token-efficient than graph-heavy memory stacks. | Vendor/project materials and planned baseline | Independent reproduction required | No | High | Counterhypothesis |
| Graphiti is suitable for temporal facts and provenance. | Project docs/papers and planned integration | Adapter evaluation required | No | Medium | Candidate |
| Graphiti adapter contract exists; real Graphiti integration is not implemented. | Local adapter package | Code/interface inspection | Yes | Low | Supported |
| Mem0 adapter contract exists and guarded optional Mem0 baseline code is present. | Local adapter package | Code/interface inspection and fake-client tests | Yes, without live service | Medium | Supported as integration path, not benchmark evidence |
| Letta adapter contract exists; real Letta integration is not implemented. | Local adapter package | Code/interface inspection | Yes | Low | Supported |
| Graphiti integration works. | None | Unsupported | No | High | Rejected |
| Mem0 live benchmark results exist for this repo. | None | Unsupported unless `--include-mem0` runs with configured service | No | High | Rejected |
| Letta integration works. | None | Unsupported | No | High | Rejected |
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
