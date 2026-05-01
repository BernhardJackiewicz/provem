# Graphiti Live Evaluation Setup

Graphiti is optional. The default benchmark, tests and quality gate must run
without Graphiti, Neo4j, Docker, API keys or real data.

The live Graphiti comparison is only valid after a real adapter implementation
exists and a smoke test succeeds. At this checkpoint, `GraphitiBackend` is still
a stub, so setup checks and smoke failures are readiness evidence only.

## Safety Rules

- Do not commit `.env`, Neo4j data, memory snapshots or real transcripts.
- Use only fake benchmark-style data for the first live smoke test.
- Do not use real PII.
- Do not print secret values in logs or reports.
- Do not treat a skipped or failed setup as a benchmark result.

## Environment Check

Run either command:

```bash
PYTHONPATH=src python3 -m cognitive_memory graphiti-env-check
PYTHONPATH=src python3 scripts/check_graphiti_env.py
```

The checker reports:

- Python version.
- Whether `graphiti` or `graphiti_core` is importable.
- Whether Neo4j connection environment variables are present.

It never prints password values.

## Python Setup

Graphiti readiness expects Python 3.10 or newer.

Example local setup:

```bash
python3.10 -m venv .venv-graphiti
source .venv-graphiti/bin/activate
python -m pip install -U pip
python -m pip install -e ".[graphiti]"
```

## Neo4j Setup

You can use an existing local Neo4j instance or the optional compose file:

```bash
export GRAPHITI_NEO4J_PASSWORD="change-me-local-only"
docker compose -f docker-compose.graphiti.yml up -d
```

Then set local environment variables in your shell or ignored `.env`:

```env
GRAPHITI_NEO4J_URI=bolt://localhost:7687
GRAPHITI_NEO4J_USER=neo4j
GRAPHITI_NEO4J_PASSWORD=change-me-local-only
```

The compose file uses tmpfs for data/logs. It is intended for disposable local
testing only.

## Smoke Test

Run:

```bash
PYTHONPATH=src python3 scripts/smoke_graphiti.py
```

Current expected behavior:

- If Python, package or Neo4j config is missing, the script exits `2` and lists
  setup blockers.
- If setup is present but `GraphitiBackend` remains a stub, the script exits
  `2` and says no live Graphiti result was produced.
- It must not exit `0` until a future real adapter can write/query fake data.

The fake smoke data is intentionally non-PII:

```text
FACT user|work_mode|hybrid
```

## Current Status

Live Graphiti evaluation is still blocked. The repo currently has:

- Graphiti mapping documentation.
- Local parity backend and tests.
- Environment checker.
- Optional Neo4j compose file.
- Guarded smoke script.

It does not yet have:

- a real Graphiti adapter implementation,
- a successful live Graphiti smoke test,
- Graphiti benchmark results,
- production persistence or deletion semantics in Neo4j.
