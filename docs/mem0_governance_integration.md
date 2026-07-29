# Mem0 governance integration (live smoke)

`Mem0ReliabilityBackend` (`src/cognitive_memory/adapters/mem0_reliability.py`)
implements the `MemoryBackend` protocol from `reliability.py`, so
`GovernedMemory` runs unchanged on a real Mem0 platform store. This turns the
backend-agnostic *contract* into a runnable deployment artifact.

## What is proven live

Run against the real Mem0 platform (`mem0ai` 2.0.14, Python 3.11), 4 tests, all
passing, with per-run namespace and `purge()` cleanup in teardown:

- `remember` → `recall_value` roundtrip returns the stored value.
- **Erasure holds despite backend latency/refusal.** `forget()` blocks the value
  read-side even though Mem0 deletes asynchronously and may still list it — the
  governance layer does not depend on the backend actually deleting.
- **Scope isolation across tenants.** A tenant is never served another tenant's
  value (tenant → namespaced Mem0 `user_id`).
- **Injection payload never served.** A record carrying override text arriving
  from a low-trust `external_tool` source is not returned as an answer.

## Design note: robust against paraphrase

Mem0 extracts/rewrites stored memories, so governance never trusts Mem0's
`memory` string. Every governance field (original text, subject/relation/object,
source, trust, provenance, validity, quarantine state) is mirrored verbatim into
Mem0 `metadata`, and records are reconstructed from that metadata. Erasure
tombstones match against the mirrored `engram_text`, not Mem0's paraphrase.

## Honest scope

This is a **smoke test, not a benchmark**. It uses a handful of API calls on the
free tier; it does not measure throughput, recall quality, or accuracy against
Mem0. Offline coverage (`tests/test_mem0_reliability_adapter.py`, 7 tests) runs
in the default environment with a fake client, including the
delete-refused-still-enforced case. The live tests
(`tests/test_mem0_integration.py`) skip unless `MEM0_API_KEY` is set and
`mem0ai` is importable.

## Reproduce

```
python3.11 -m venv venv && ./venv/bin/pip install mem0ai
MEM0_API_KEY=... PYTHONPATH=src ./venv/bin/python -m unittest tests.test_mem0_integration
```
