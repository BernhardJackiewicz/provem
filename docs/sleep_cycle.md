# Sleep Cycle / Consolidation

MVP 3.0 starts as a local dry-run proposal engine. It is not biological sleep,
not an autonomous agent and not a production consolidation system.

## Current Behavior

`SleepCycle` scans local episodes, temporal facts, memory events and existing
reflections, then returns a `ConsolidationRun` with candidates and decisions.
By default it does not write durable facts, events or reflections.

Supported decision actions:

- `create_reflection`
- `update_reflection`
- `archive`
- `decay`
- `flag_conflict`
- `no_op`

Every decision carries evidence ids, counter-evidence ids, confidence, scope,
memory type, reason and `review_required`.

## Safety Rules

- Single weak evidence does not create a stable reflection.
- Deleted and do-not-use memories are ignored.
- Prompt-injection-like content is not consolidated as instruction.
- Sensitive memories require review and are not turned into stable reflection.
- Conflicting evidence produces `review_required` conflict decisions.
- Candidate, client, role, project and user scope are preserved.
- Superseded facts produce decay/archive proposals only; they do not become
  current truth again.

## CLI

```bash
PYTHONPATH=src python3 -m cognitive_memory sleep-cycle --demo
```

The demo uses fake local data and prints counts for candidates, decisions and
review-required items. It avoids raw sensitive values in the human summary.

```bash
PYTHONPATH=src python3 -m cognitive_memory sleep-cycle --demo --json
```

JSON output is intended for local debugging of fake data. Do not use it for real
PII.

`--apply` is reserved and currently exits with a clear error. Durable apply
needs a separate design and tests.

## Consolidation Evaluation

MVP 3.1 adds a local evaluation harness:

```bash
PYTHONPATH=src python3 -m cognitive_memory consolidation-eval
```

It compares three modes on fake scenarios:

- `no_consolidation`
- `sleep_cycle_dry_run_only`
- `simulated_human_approved_consolidation`

The simulated approval mode applies only conservative decisions to an isolated
evaluation copy. It rejects review-required proposals, stale/superseded
evidence, prompt-injection-like text, sensitive content and
conflict-overlapping scopes.

MVP 3.2 adds scope-aware reflection metadata so safe simulated approval can
represent candidate-, client-, role-, project- and user-scoped consolidated
memories without flattening them into global claims. A reflection or
consolidated memory now preserves:

- `user_id`
- `project_id`
- `candidate_id`
- `client_id`
- `role_id`
- `actor_type`
- `subject_id`
- `relation_type`
- `scope_confidence`
- `reflection_type`

Supported reflection types are:

- `user_preference`
- `candidate_preference`
- `client_requirement`
- `role_requirement`
- `project_pattern`
- `procedural_rule`
- `risk_warning`
- `unresolved_hypothesis`

SleepCycle may propose scoped reflections only when evidence stays in the same
scope, actor type and relation family. Retrieval excludes wrong-scope
reflections before ranking, using the same conservative stance as temporal
facts.

This harness reports downstream task delta, consolidation precision/recall,
unsafe consolidation, overgeneralization, stale fact resurrection,
review-required accuracy, provenance coverage, policy violation and scope
leakage. It is not a production apply workflow and does not mutate durable
memory outside the evaluation copy.

## Persistence

JSONL snapshots can store `consolidation_run` records when a caller explicitly
records them. These records are audit/proposal artifacts, not applied memory.

## Limitations

- No live LLM extraction is used.
- No Graphiti, Mem0 or Letta integration is used.
- No human review UI exists.
- Decay is metadata/report-only and does not change retrieval ranking.
- Scoped reflection approval exists only inside the evaluation copy.
- The proposal rules are deterministic and can miss natural paraphrases or true
  long-range patterns.
- MVP 3.2 tests usefulness only on fake local scenarios. It does not prove that
  consolidation improves real-world task performance.
