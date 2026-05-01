# Mem0 Live Evaluation Setup

Mem0 is optional. The default benchmark, tests and quality gate must run without
Mem0 installed, without API keys and without real data.

The live Mem0 benchmark is only valid when this command succeeds:

```bash
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0 --strict-optional
```

Skipped runs, fake-client tests and setup failures are not live Mem0 evidence.

## Safety Rules

- Do not commit API keys, local configs, memory snapshots or real transcripts.
- Use only synthetic benchmark scenarios for the first live Mem0 comparison.
- Keep `.env` local and ignored.
- Do not print secret values in logs or reports.
- Do not use real PII.

## Environment Checker

Run:

```bash
PYTHONPATH=src python3 -m cognitive_memory mem0-env-check
```

or:

```bash
PYTHONPATH=src python3 scripts/check_mem0_env.py
```

The checker reports Python version, Mem0 importability, selected mode, required
environment-variable presence, Qdrant reachability for OSS mode and embedding
model availability when it can detect it. It never prints key values.

## Platform Mode

Platform mode uses `mem0.MemoryClient`.

Requirements:

- Python 3.10 or newer.
- `python3 -m pip install -e ".[mem0]"`
- `MEM0_API_KEY` set in the shell or ignored local `.env`.

Example ignored `.env`:

```env
MEM0_API_KEY=...
```

Validation:

```bash
PYTHONPATH=src python3 -m cognitive_memory mem0-env-check
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0 --strict-optional
PYTHONPATH=src python3 -m cognitive_memory mem0-sanity --strict-optional
```

## OSS Local Mode

OSS mode uses `mem0.Memory.from_config`.

Requirements:

- Python 3.10 or newer.
- `python3 -m pip install -e ".[mem0]"`
- `MEM0_MODE=oss`
- `MEM0_OSS_CONFIG_PATH` pointing to a local Mem0 config.
- A reachable vector store, currently checked as Qdrant on `127.0.0.1:6333`
  unless `QDRANT_HOST` / `QDRANT_PORT` are set.
- A configured local or self-hosted LLM and embedder.

Example ignored `.env`:

```env
MEM0_MODE=oss
MEM0_OSS_CONFIG_PATH=/path/to/local/mem0_config.json
MEM0_OSS_EMBEDDING_MODEL=nomic-embed-text
```

The checker can detect Ollama models from `ollama list` when Ollama is
available. If no embedding model is configured or detection is unavailable, the
checker reports that limitation instead of inventing a result.

Validation:

```bash
PYTHONPATH=src python3 -m cognitive_memory mem0-env-check
PYTHONPATH=src python3 -m cognitive_memory benchmark --suite all --include-mem0 --strict-optional
```

## Write Settling and Cleanup

Mem0 Platform may queue memory writes. For write-before-read fairness, the
adapter requests `async_mode=False` when the SDK accepts it and waits before the
first search after a queued write. The default wait for real Mem0 clients is two
seconds and can be changed locally:

```bash
export MEM0_WRITE_SETTLE_SECONDS=2
```

Injected fake clients used in tests do not wait by default.

The benchmark also uses synthetic per-run/per-scenario namespaces and attempts
best-effort `delete_all(user_id=...)` cleanup when the SDK supports it. Cleanup
failures do not make the benchmark fail because namespace isolation is the
primary safety boundary.

Live all-suite runs can consume substantial Mem0 Platform quota because each
isolated scenario writes memories, waits, searches and then attempts cleanup.
If the API returns a quota or rate-limit error, stop and record the run as
incomplete rather than reporting partial results as benchmark evidence.

## Current Status

At this checkpoint, a Mem0 Platform live comparison has completed on synthetic
benchmark data only. The repo has:

- a guarded adapter path,
- fake-client tests,
- setup checking,
- documentation for platform and OSS modes,
- one no-PII live Platform result recorded in `docs/mem0_comparison.md`.

The successful run used a temporary Python 3.11 environment and Mem0 Platform.
The system `python3` remains Python 3.9.6 and does not have `mem0`/`mem0ai`
installed, so the default local test path remains dependency-free and cannot run
Mem0 live without an explicit Python 3.10+ environment.

The live result is synthetic-only evidence. The fairness audit currently shows
Mem0 can retrieve some simple memories after write settling, but the generic
adapter/query setup still has answer-format mismatch and governance gaps. It
does not validate real PII, external datasets, production integration, or native
Mem0 governance behavior. `MEM0_API_KEY` may be present locally, but secret
values must never be printed or committed.
