# MVP 3 Quality Gate Draft

MVP 3.0 is acceptable only as a local dry-run consolidation prototype. MVP 3.1
adds an evaluation harness for proposal usefulness and safety. MVP 3.2 adds
scope-aware reflection metadata for candidate/client/role/project/user
consolidation. MVP 3.3 adds a local human-review queue and simulated reviewer
policies. Durable apply behavior is still not implemented.

## Pass Criteria

- `SleepCycle` returns `ConsolidationRun` proposals without durable memory
  writes by default.
- `--apply` is reserved and fails clearly.
- Repeated evidence can propose a reflection.
- Single weak evidence produces no stable reflection proposal.
- Deleted, do-not-use and prompt-injection-like memories are not consolidated.
- Sensitive evidence requires review.
- Conflicts create review-required decisions.
- Candidate/client/project/role scope is preserved.
- Superseded facts create decay/archive proposals, not current truth.
- Consolidation runs can round-trip through JSONL when explicitly recorded.
- `consolidation-eval` compares no consolidation, dry-run proposals and
  simulated approval in an isolated evaluation copy.
- Simulated approval rejects unsafe, stale, conflicting, forbidden, sensitive or
  unresolved consolidation decisions.
- Scope-aware reflections preserve `candidate_id`, `client_id`, `role_id`,
  `project_id`, `actor_type`, `subject_id`, `relation_type`,
  `scope_confidence` and `reflection_type`.
- Wrong-scope reflections are excluded before ranking.
- Scoped consolidation metrics report zero cross-scope, role-scope and
  candidate/client reflection leakage on the local fake suite.
- Scoped consolidation precision and recall are explicit metrics, not inferred
  from general downstream accuracy.
- Consolidation evaluation reports zero unsafe consolidation, policy leakage,
  stale resurrection and scope leakage on the local fake suite before any future
  apply workflow is considered.
- ReviewQueue can be built from SleepCycle decisions without writing durable
  memory.
- ReviewQueue preserves consolidation run links, decision links, evidence ids,
  counter-evidence ids, scope, risk level and proposed action.
- High-risk review items are never auto-approved by local simulation.
- `review-queue --demo` prints status/risk/reason counts without raw sensitive
  values.
- Consolidation evaluation reports clean review metrics:
  `review_queue_precision`, `review_queue_recall`, `approval_precision`,
  `unsafe_approval_rate`, `high_risk_autoapproval_rate`, `review_coverage` and
  `review_to_downstream_delta`.
- Review calibration reports:
  `low_risk_approval_rate`, `medium_risk_review_rate`,
  `high_risk_rejection_rate`, `useful_review_item_rate`,
  `over_conservative_rejection_rate` and `approval_downstream_delta`.
- At least one useful low-risk item is approved in local simulation while
  high-risk autoapproval remains zero.

## Fail Criteria

- SleepCycle silently writes durable facts, events or reflections.
- Decay changes retrieval ranking without explicit tests.
- Sensitive or forbidden content is summarized into a stable reflection.
- Candidate and client scopes are merged.
- Role-scoped reflections answer other-role queries.
- Client requirements become candidate preferences, or candidate preferences
  become client requirements.
- Simulated approval writes into the live controller/store.
- ReviewQueue simulation writes durable facts, events or reflections.
- High-risk items are approved by `approve_safe` or `approve_low_risk_only`.
- The demo cannot approve any clearly low-risk item in simulation.
- `consolidation-eval` treats missing provenance or unavailable safety fields
  as successful evidence.
- Docs imply biological fidelity, production readiness or live integration
  evidence.

## Required Local Verification

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all
PYTHONPATH=src python3 -m cognitive_memory transcript-eval --input tests/fixtures/transcripts
PYTHONPATH=src python3 -m cognitive_memory sleep-cycle --demo
PYTHONPATH=src python3 -m cognitive_memory consolidation-eval
PYTHONPATH=src python3 -m cognitive_memory review-queue --demo
PYTHONPATH=src python3 -m cognitive_memory quality-gate
PYTHONPATH=src python3 -m compileall -q src tests
```
