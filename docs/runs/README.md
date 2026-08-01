# Frozen run artifacts

These files back the €0 replay: `sh scripts/verify_repro.sh` re-derives and
**asserts** every published number from them. `docs/runs/manifest.json` pins
their sha256 hashes (`python3 scripts/build_manifest.py --check`).

| Path | What it is |
|---|---|
| `locomo_e2e_ours_vs_mem0.jsonl` | Campaign-1 baseline predictions (ours + Mem0), pre-optimization |
| `locomo_e2e_ours_optimized.jsonl` | Campaign-1 optimized ours arm (dense+strict4) |
| `locomo_e2e_ours_emptyfix.jsonl` | Ours arm after the symmetric empty-prediction fix |
| `locomo_e2e_mem0_patched.jsonl` | Mem0 arm after the symmetric empty-prediction fix (headline Mem0 row) |
| `zep_locomo.jsonl` | Zep arm predictions (headline Zep row) |
| `iter/*.jsonl` | Every campaign-2 iteration incl. failures; `c2_full_final2.jsonl` is the headline Provem row, `c2_v2_neutral.jsonl` the neutral-prompt control |
| `caches/llm_cache_answer.jsonl` | Frozen answerer outputs (keyed by context hash) |
| `caches/llm_cache_judge.jsonl` | Frozen strict-judge verdicts (backfilled to match the stored rows; see `scripts/backfill_judge_cache.py`) |
| `caches/judge2_cache.jsonl` | Frozen claude-opus-5 verdicts |
| `caches/mem0_judge_cache.jsonl` | Frozen verdicts under Mem0's own judge prompt |
| `caches/campaign_ledger.jsonl` | Per-run API spend ledger (EUR) |
| `caches/vector_cache.jsonl` | 109 MB embeddings cache, **Git LFS**. Only needed to re-run dense retrieval itself; every published-number replay works without it (re-embedding from scratch costs cents and an API key) |
| `manifest.json` | Reproducibility freeze: artifact hashes, models, prompt versions, scoreboard |

Not distributed: `data/external/locomo/locomo10.json` (CC BY-NC 4.0) — obtain it
with `sh scripts/fetch_locomo.sh` (sha256-verified against the manifest).
Ignored: `docs/runs/local/` (scratch outputs of fresh runs), `locomo_*.json`
per-QA diagnostic dumps, `*.err` raw failure exports.
