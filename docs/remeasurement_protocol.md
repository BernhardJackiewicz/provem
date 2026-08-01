# Preregistered protocol: corrected re-measurement of the Mem0 and Zep arms (v2)

Committed BEFORE the first API call of the campaign. Any deviation must be
logged in `docs/measurement_changelog.md` before continuing.

## 1. Motivation — two ingestion bugs made the v1 baselines unfair

1. **Mem0 double date prefix.** `build_ours()` mutated `sample.episodes[*].content`
   in place to `dated_content(ep)` (v1 `scripts/mem0_locomo_e2e.py:293`), the main
   loop called it before `mem0_ingest`, and `mem0_ingest` applied `dated_content`
   again (v1 line 471, no idempotence guard in `dated_content`, v1 lines 270-274).
   Mem0 therefore ingested every turn as `"8 May 2023 | 8 May 2023 | text"` while
   Provem stored the single-prefixed text. Every v1 Mem0 number is built on that
   malformed input.
2. **Zep lexical session order.** `scripts/zep_locomo.py` (v1 line 81) iterated
   `sorted(_sessions(sample).items())` — a string sort: D1, D10, D11, …, D19, D2 —
   in all 10 conversations, feeding Zep's temporal knowledge graph episodes in the
   wrong arrival order. Additionally the v1 eval ran with 94/5,882 episodes never
   processed (no completion gate).

Both bugs are fixed in commit `45eb588` ("Measurement fixes for the corrected
re-measurement campaign (v2)"), together with a completion gate, fail-loud
ingestion, and a true `--systems mem0` mode. Byte-identity of the frozen Provem
arm under the fix was proven via cache-hit probes (zero API calls).

**The Provem arm is NOT re-run.** Its frozen predictions
(`docs/runs/iter/c2_full_final2.jsonl`) are reused unchanged, including their
known, disclosed asymmetry (dev-tuned strict5 prompts; the abstention confound
is disclosed in the README and `docs/three_system_benchmark.md`).

## 2. Frozen elements

| Element | Value |
|---|---|
| Provem predictions | `docs/runs/iter/c2_full_final2.jsonl`, sha256 `4b952471ba48790c3409505f99e3acebc226d31ded1ea60f8741ff1c6e9f7e21` |
| Dataset | `data/external/locomo/locomo10.json`, sha256 `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4` |
| Mem0 judge template | `scripts/prompts/mem0_judge_template.txt`, sha256 `d248e056d993725e28fba8d16ca7081f0b59deae272ef294f3c6b00d48eac02b` |
| Code state | commit `45eb588` (measurement fixes) on top of the WS1 offline-fix series |
| SDKs (`.venv-bench`, python 3.13.13) | `mem0ai==2.0.14`, `zep-cloud==3.25.0`, `openai==2.52.0` |

## 3. Fixed configuration (identical to v1 wherever v1 was correct)

- Answerer: `gpt-5-mini`, `reasoning_effort=medium`, **neutral v1 prompt**
  (`ANSWER_SYS`) for both baseline arms — no strict prompts, no prompt changes.
- Strict judge: `gpt-5`, `reasoning_effort=low`, shared `JUDGE_SYS` verbatim.
- Second judge: `claude-opus-5` (same wording); third judge: `gpt-5` with Mem0's
  template above.
- Retrieval budget: `top_k=20` (Zep: edges 20 + nodes 10, as v1).
- Snapshot pinning: immediately before Phase M1, one 1-token probe call per
  model records the provider's concrete snapshot id (`response.model`) into the
  changelog and `manifest.json:model_snapshots`.
- uid prefixes: `engram_e2e_v2` (Mem0), `provem_zep_v2` (Zep) — fresh stores;
  the v1 stores are left untouched.
- Outputs: `docs/runs/locomo_e2e_mem0_v2.jsonl`, `docs/runs/zep_locomo_v2.jsonl`.
- Opus cache set names: `mem0_v2`, `zep_v2`; m0j keys system-agnostic (by design).
- Budget: new ledger `docs/runs/caches/remeasure_ledger.jsonl`, hard cap
  `--campaign-cap-eur 40`. The v1 ledger (€29.85) is closed.

## 4. One-run rule

One execution per arm. Crash/rate-limit/budget restarts resume via done-row
replay and caches only. **No configuration change of any kind after the first
API call.** Results are published regardless of direction; if the corrected
baselines close or invert any gap, the README and benchmark docs are updated to
say exactly that.

## 5. Cache policy

The shared `docs/runs/caches/llm_cache_answer.jsonl` / `llm_cache_judge.jsonl`
are reused: answer keys are context-hashed (re-ingested stores produce new
contexts → natural misses → fresh answers), strict-judge keys are
prediction-based and system-agnostic (hits are legitimate — the judge sees only
question+gold+prediction — and free). Per stage, needed/cached/ran counts are
recorded in the changelog (`wc -l` before/after each cache).

## 6. Exact commands

```bash
set -a; . ./.env; set +a
# M0 snapshot probe (3 calls, ~EUR 0.01) — records model snapshot ids
# M1 Mem0 arm (~3-4 h, ~EUR 2-4)
.venv-bench/bin/python scripts/mem0_locomo_e2e.py \
  --systems mem0 --uid-prefix engram_e2e_v2 --include-abstain \
  --out docs/runs/locomo_e2e_mem0_v2.jsonl --cache-dir docs/runs/caches \
  --ledger docs/runs/caches/remeasure_ledger.jsonl --campaign-cap-eur 40 \
  --wait 600 --workers 12 --tag mem0_v2_remeasure
# M2 Zep arm, credit-staged per conversation (see §7)
.venv-bench/bin/python scripts/zep_locomo.py --mode ingest --convs <ci> --uid-prefix provem_zep_v2
.venv-bench/bin/python scripts/zep_locomo.py --mode wait   --convs <ci> --uid-prefix provem_zep_v2
.venv-bench/bin/python scripts/zep_locomo.py --mode eval   --convs <ci> --uid-prefix provem_zep_v2 \
  --out docs/runs/zep_locomo_v2.jsonl --cache-dir docs/runs/caches --workers 4
# M3 judges (pilot first)
.venv-bench/bin/python scripts/remeasure_judges.py --pilot 30
.venv-bench/bin/python scripts/remeasure_judges.py --workers 8
# M4 report (EUR 0, run twice, outputs must be byte-identical)
.venv-bench/bin/python scripts/three_system_report.py \
  --mem0 docs/runs/locomo_e2e_mem0_v2.jsonl --zep docs/runs/zep_locomo_v2.jsonl \
  --opus-sets provem=ours_opt,mem0=mem0_v2,zep=zep_v2 \
  --out docs/runs/three_system_report_v2.json
```

## 7. Zep credit staging and abort criteria

Zep account: FREE plan, 10,000 flex credits/month, 2,463 used at protocol time
(7,537 free; resets Sept 1). Episodes bill 1 credit each; whether eval graph
searches bill is unknown → measured, not assumed.

- Stage A = conv 0 (419 episodes): ingest → wait → dashboard reading `C_ing0`
  (ingest rate `r_i = (C_ing0 − C_start)/419`) → eval conv 0 → reading `C_eval0`
  (search rate `r_s = (C_eval0 − C_ing0)/(2·Q0)`).
- Projection: `total = C_eval0 + r_i·(5882−419) + r_s·2·(1986−Q0)`.
- **HARD GATE: proceed only if projection ≤ 9,700** (300-credit reserve);
  otherwise abort the Zep arm and resume after the Sept 1 reset (graphs and
  ledgers persist). Re-projection checkpoints after convs 3 and 6.
- Graph completion: `--mode eval` hard-fails below 100% processed. Stall rule:
  if polling stalls ≥ 30 min at ≥ 99.5%, wait 24 h and re-poll once — still
  stalled at ≥ 99.5% → proceed with the shortfall disclosed in the changelog;
  < 99.5% → abort the arm until reset. (Strictly better than v1's undisclosed
  98.4%.)
- Every dashboard reading is recorded in the changelog.

Mem0 abort criteria: any failed session-batch add hard-fails (code); per-conv
memory count must be > 0 and ≥ 50; error rows must be 0 in the output file.

## 8. Preregistered number list (computed only by `scripts/three_system_report.py`)

Answerable accuracy + Wilson 95% CI for 3 systems × 3 judges; abstention on the
446 adversarial questions per system; per-category strict accuracy under the
official LoCoMo names; holdout (convs 1,3,5,7,9) strict accuracy; all pairwise
exact McNemars per judge plus abstention McNemars; Cohen's κ strict-vs-opus on
LLM-judged rows. Nothing else is computed, and no number is hand-edited into a
doc — docs quote the report JSON.

## 9. Publication commitment

The v2 numbers replace the v1 numbers in README and
`docs/three_system_benchmark.md` whatever they show. v1 numbers move to
`docs/measurement_changelog.md` as the historical record with the bug
root-causes. The standing disclosed asymmetry (Provem's dev-tuned prompts,
abstention confound) carries over unchanged unless v2 measurement makes it moot.
