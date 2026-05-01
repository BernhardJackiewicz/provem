# Adapter Architecture

This repo prepares MVP 2 without making external systems mandatory.

## Contracts

- `TemporalGraphBackend`: Graphiti-like temporal fact/event storage and search.
- `ExternalMemoryBackend`: Mem0-like external memory ingestion and retrieval.
- `StatefulAgentBackend`: Letta-like stateful agent orchestration.
- `ExtractorPort`: deterministic or schema-constrained memory extraction.

## Implementations

- `LocalTemporalGraphBackend`: default in-memory backend used by the controller.
- `LocalGraphitiParityBackend`: dependency-free Graphiti-contract parity
  backend. It uses the same local store and policy gate as
  `LocalTemporalGraphBackend`, but exposes Graphiti-like capability flags and
  query/write method names for semantic parity tests.
- `MockGraphitiBackend`: contract mock only; not Graphiti.
- `MockMem0Backend`: deterministic lexical mock only; not Mem0.
- `MockLettaBackend`: deterministic orchestration mock only; not Letta.
- `GraphitiBackend`: explicit stub; raises until real service integration is implemented.
- `Mem0Backend`: guarded optional baseline adapter. It lazily imports
  Mem0, supports injected clients for tests, and is not used by default.
  Platform mode uses `mem0.MemoryClient`; OSS mode uses `mem0.Memory`.
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

For Mem0 Platform, install the extra and set `MEM0_API_KEY`.

For Mem0 OSS/local, install the extra and set:

```bash
export MEM0_MODE=oss
export MEM0_OSS_CONFIG_PATH=/path/to/local/mem0_config.json
```

That config must provide working Mem0 providers. Official Mem0 docs describe
OpenAI defaults for `Memory()` and Ollama-based local configurations for
`Memory.from_config`; both still require a complete local/provider setup before
the benchmark can run. A missing package, missing API key, missing config,
missing embedding model or unavailable vector store is a setup failure, not a
benchmark result.

If Mem0 is unavailable or unconfigured, the baseline is skipped by default with
a clear message. Use `--strict-optional` to fail instead of skipping.

For local tests, the Mem0 path uses injected fake clients and existing
structured/noisy/recruiting/adversarial scenarios. No real transcripts or PII
are used in the fake path.

Graphiti and Letta remain stubs. Graphiti now has mapping/parity scaffolding,
but it still needs real adapter code, configuration, service tests and
deletion-policy tests.

## MVP 2 Rule

The Memory Controller remains the only durable write authority. External
systems may propose memory or serve as storage backends, but they must not bypass
policy filtering, provenance checks, deletion, do-not-use, or project scoping.

The local backend now also stores `MemoryEvent` objects. A future Graphiti
adapter should map those events to graph event nodes, participants to entity
nodes and event relations to temporal edges. The detailed mapping contract is
documented in `docs/graphiti_mapping.md`; it is not implemented as a live
integration.

## Graphiti Parity Scaffold

`LocalGraphitiParityBackend` exists so future Graphiti work can be tested
against a stable local semantic contract before any Neo4j service is trusted.
It exposes these capability flags:

- `supports_temporal_facts`
- `supports_events`
- `supports_policy_metadata`
- `supports_provenance`

It also exposes Graphiti-like method names:

- `write_episode`
- `write_temporal_fact`
- `write_memory_event`
- `query_current_facts`
- `query_historical_facts`
- `query_relationships`

These methods are local wrappers around the existing store and retrieval
planner. They are useful for parity tests only. They do not prove Graphiti API
compatibility, latency, scale, deletion propagation or Neo4j operations.

Run the optional Graphiti setup check:

```bash
PYTHONPATH=src python3 -m cognitive_memory graphiti-env-check
```

The checker reports Python version, Graphiti package importability and Neo4j
environment-variable presence without printing secret values. It is a readiness
check only, not live validation.

## Mem0 Baseline Limits

The Mem0 adapter receives the same episodes and retrieval requests as the other
baselines. It does not receive expected outputs. Results are only valid when a
real configured Mem0 backend actually runs; skipped optional runs are not
benchmark evidence.

The current environment used for this checkpoint did not produce a live Mem0
result: the runtime is Python 3.9.6, `mem0ai` is not installed and a complete
Mem0 Platform or OSS provider stack has not been validated. Mem0 live
comparison is deferred until a Python 3.10+ environment and Mem0 runtime are
available.

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
