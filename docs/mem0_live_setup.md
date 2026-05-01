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

## Current Status

At this checkpoint, Mem0 live comparison is blocked/deferred, not complete. The
repo has:

- a guarded adapter path,
- fake-client tests,
- setup checking,
- documentation for platform and OSS modes.

It does not yet have a successful live Mem0 benchmark result. The current
environment is not sufficient for live comparison because it does not provide a
validated Python 3.10+ Mem0 runtime and a complete Mem0 Platform or OSS setup.
Skipped runs and fake-client tests remain readiness checks only.
