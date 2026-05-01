# MVP 3 Quality Gate Draft

MVP 3.0 is acceptable only as a local dry-run consolidation prototype. MVP 3.1
adds an evaluation harness for proposal usefulness and safety, still without
durable apply behavior.

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
  unrepresentable scoped consolidation decisions.
- Consolidation evaluation reports zero unsafe consolidation, policy leakage,
  stale resurrection and scope leakage on the local fake suite before any future
  apply workflow is considered.

## Fail Criteria

- SleepCycle silently writes durable facts, events or reflections.
- Decay changes retrieval ranking without explicit tests.
- Sensitive or forbidden content is summarized into a stable reflection.
- Candidate and client scopes are merged.
- Simulated approval writes into the live controller/store.
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
PYTHONPATH=src python3 -m cognitive_memory quality-gate
PYTHONPATH=src python3 -m compileall -q src tests
```
