# Adapter Architecture

This repo prepares MVP 2 without making external systems mandatory.

## Contracts

- `TemporalGraphBackend`: Graphiti-like temporal fact/event storage and search.
- `ExternalMemoryBackend`: Mem0-like external memory ingestion and retrieval.
- `StatefulAgentBackend`: Letta-like stateful agent orchestration.
- `ExtractorPort`: deterministic or schema-constrained memory extraction.

## Implementations

- `LocalTemporalGraphBackend`: default in-memory backend used by the controller.
- `MockGraphitiBackend`: contract mock only; not Graphiti.
- `MockMem0Backend`: deterministic lexical mock only; not Mem0.
- `MockLettaBackend`: deterministic orchestration mock only; not Letta.
- `GraphitiBackend`: explicit stub; raises until real service integration is implemented.
- `Mem0Backend`: guarded optional baseline adapter. It lazily imports
  `mem0.MemoryClient`, supports injected clients for tests, and is not used by
  default.
- `LettaBackend`: explicit stub; raises until real service integration is implemented.

## Optional Extras

`pyproject.toml` defines optional extras for future work:

```bash
python3 -m pip install -e ".[graphiti]"
python3 -m pip install -e ".[mem0]"
python3 -m pip install -e ".[letta]"
```

Installing an extra does not change default behavior.

Mem0 can be tried as an optional external baseline:

```bash
MEM0_API_KEY=... PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0
```

If Mem0 is unavailable or unconfigured, the baseline is skipped by default with
a clear message. Use `--strict-optional` to fail instead of skipping.

For local tests, the Mem0 path uses injected fake clients and existing
structured/noisy/recruiting/adversarial scenarios. No real transcripts or PII
are used in the fake path.

Graphiti and Letta remain stubs. They still need real adapter code,
configuration, service tests and deletion-policy tests.

## MVP 2 Rule

The Memory Controller remains the only durable write authority. External
systems may propose memory or serve as storage backends, but they must not bypass
policy filtering, provenance checks, deletion, do-not-use, or project scoping.

The local backend now also stores `MemoryEvent` objects. A future Graphiti
adapter should map those events to graph event nodes, participants to entity
nodes and event relations to temporal edges. That mapping is documented in
`docs/event_model.md`; it is not implemented as a live integration.

## Mem0 Baseline Limits

The Mem0 adapter receives the same episodes and retrieval requests as the other
baselines. It does not receive expected outputs. Results are only valid when a
real configured Mem0 backend actually runs; skipped optional runs are not
benchmark evidence.

Normalized Mem0 benchmark records expose:

- `answer`
- `selected_memories`, when the backend returns search memories
- `provenance`, when Mem0 result metadata includes source episode IDs
- `abstention`, derived from empty search results unless Mem0 exposes native
  abstention semantics later
- leakage flags computed by the shared benchmark evaluator
- latency measured by the benchmark runner

If a field is not exposed by Mem0, the benchmark marks it unavailable through
`normalized_fields` rather than counting it as successful evidence.
