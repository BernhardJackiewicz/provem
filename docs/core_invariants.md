# Core Invariants

MVP 1.7 adds reliability guards around the memory core. These invariants are
more important than recall. A change that improves benchmark accuracy while
violating one of these rules is a regression.

## Non-Negotiable Rules

- Deleted memory is never selected.
- Do-not-use memory is never selected.
- Prompt-injection-like memory is never treated as an instruction or selected
  as ordinary usable memory.
- Wrong-scope memory is excluded before ranking can make it attractive.
- Source conflicts cause abstention unless a local precedence rule is explicit.
- Superseded facts do not resurrect as current truth.
- Historical queries may access historical facts only when the query explicitly
  asks for an as-of date.
- Every selected memory has provenance.
- Every excluded memory has an exclusion reason.
- Facts, events and reflections go through the same policy gate.
- Persistence reload must not change retrieval results.
- Replaying the same episode sequence must not create duplicate active truth.
- Adding irrelevant distractor memory must not make unsafe memory selectable.

## Guarded By Tests

`tests/test_invariants.py` covers deterministic invariants for deletion,
do-not-use, source conflicts, prompt-injection quarantine, wrong-scope
exclusion, supersession, event/reflection policy gates, provenance, exclusion
reasons, persistence round trips and replay/idempotency.

The same file also runs 100 seeded fuzz sequences with deterministic memory
operations:

- fact writes
- supersession
- deletion
- do-not-use
- source conflicts
- wrong-scope facts
- prompt-injection-like facts
- event-producing facts
- reflection insertion
- snapshot export/import
- current and historical retrieval

The fuzz tests compare semantic retrieval results rather than UUIDs. UUIDs are
expected to differ; selected claims, abstention behavior and exclusion reasons
must remain stable for the same seed.

## Persistence Contract

JSONL snapshots now include `schema_version` on exported records. The importer:

- accepts current v1 records
- accepts old/minimal records without `schema_version` as v1 when the record
  type is known
- rejects future schema versions clearly
- rejects unknown record types

This is research persistence, not production migration support. It has no
encryption, locking, access control, concurrent write safety or retention
enforcement.

## Quality Gate

Run:

```bash
PYTHONPATH=src python3 -m cognitive_memory quality-gate
```

The command checks the synthetic benchmark, transcript fixtures and a local
persistence smoke test. It deliberately does not run the full unittest suite;
`python3 -m unittest discover -s tests -v` remains the authoritative test
command.

## Do Not Weaken Casually

- Controller-only durable writes.
- Policy filtering before selection.
- Safe abstention for source conflict, ambiguous identity and forbidden memory.
- Provenance requirements.
- Redacted transcript summary behavior.
- Snapshot reload safety.

Future Graphiti, Letta, Mem0 or LLM extraction work must preserve these
invariants before being considered part of MVP 2.
