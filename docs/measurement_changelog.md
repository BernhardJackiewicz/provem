# Measurement changelog

Append-only record of every measurement campaign, its root causes, cache/credit
accounting, and old-vs-new numbers. The preregistered protocol for the v2
campaign is [`remeasurement_protocol.md`](remeasurement_protocol.md).

## Judge-cache backfill (2026-08-01, offline, WS1-B3)

`docs/runs/caches/llm_cache_judge.jsonl` was made authoritative for the stored
per-row strict verdicts (`scripts/backfill_judge_cache.py`).

- pre sha256: `664f1866bc815ef21907022db2aa6583679f1db3cfdb298b65cbb9fd16a40294`
- post sha256 (after backfill): `cca727f070f241c1f4143e1ee634ee8fe6a8b3568c48aefeb35226e12aac7688`
- 755 missing strict verdicts backfilled (all Mem0 rows) + 1 override
  (`conv-42_q185`, cache `False` → stored `True`).
- Resolution rule: stored per-row verdicts ARE the published record; the cache
  is appended to match (DiskCache is last-line-wins). Cache-preferred scoring
  before the fix would have read Mem0 at 0.511 vs the published 0.509.
- 7 question ids were live-judged multiple times across campaign files with
  differing outcomes (gpt-5 judge instability at effort=low): `conv-44_q014`,
  `conv-42_q185`, `conv-42_q029`, `conv-47_q013`, `conv-26_q046`,
  `conv-50_q031`, `conv-50_q102`. The latest campaign file wins per key.

## v2 re-measurement — corrected Mem0 and Zep arms (2026-08-01)

**Motivation / root causes** (full detail in the protocol):
1. Mem0 ingested a double date prefix (`build_ours` mutated `sample.episodes`
   in place before `mem0_ingest` re-applied `dated_content`). Fixed in commit
   `45eb588`; byte-identity of the frozen Provem arm verified (all episode
   embeddings + 120/120 probed contexts hit the frozen caches, zero API calls).
2. Zep ingested sessions in lexical order (D1, D10, …, D19, D2) and evaluated
   with 94/5,882 episodes unprocessed. Fixed: chronological ordering + a
   graph-completion gate that hard-fails on an incomplete graph.

**Frozen model snapshots (probed before phase M1):**
- answerer `gpt-5-mini` → `gpt-5-mini-2025-08-07`
- primary judge `gpt-5` → `gpt-5-2025-08-07`
- second judge `claude-opus-5` (Anthropic echoes the alias)
- embeddings `text-embedding-3-small` (frozen vector cache; not re-embedded)

**Cache line counts before M1 (for hit/miss accounting):**
- `llm_cache_answer.jsonl`: 19,034 lines
- `llm_cache_judge.jsonl`: 5,899 lines

**Ledgers:** v1 campaign ledger closed at €29.85. v2 uses
`docs/runs/caches/remeasure_ledger.jsonl`, cap €40.

**Run-time deviations logged (per the one-run rule):**
- A transient `thread.create` failure (swallowed by a bare `except`) aborted the
  first Zep conv-0 ingest with a downstream "thread not found"; hardened to retry
  create (commit `11e6ec6`). The half-created probe users (`provem_zep_v2_conv0`,
  `zeptest_probe_1`) were deleted so the arm ingests from a clean state.
- A `ConnectionResetError` (transient OSError, not caught by the URLError-only
  handler) aborted the Mem0 answerer; hardened `openai_chat` to retry
  OSError/HTTPException (commit `45eb588`/`0d08ab0`). The Mem0 run resumed from
  its done-rows with the conv-0 store reused (no re-ingest).
- Mem0 v2 store counts match v1 (conv0 v2=174 vs v1=176; conv1 v2=137), so the
  double-date fix did not degrade Mem0 ingestion. Low in-log counts were
  mid-settling snapshots, not the settled totals.
- Zep credit model (from the account dashboard): "Episodes 1 flex credit per
  use", "Unlimited Graph Memories" — ingestion bills per episode, graph
  searches are unlimited (free). Conv-0 gate reading: credits 2,463 → 2,661
  (+198 for conv 0's 419 messages), so the full 10-conversation ingest
  projects to ≤ ~8,345 worst-case (5,882 messages) and ~5,000 at the measured
  rate — comfortably under the 10,000 free cap. Deviation from the per-conv
  staging gate: given the confirmed headroom and free searches, convs 1-9 were
  bulk-ingested at once so Zep processes all graphs in parallel (wall-clock ≈
  one conversation, not the sum). Ingestion order stays chronological per conv.
- **Premature-eval artifact caught and corrected (important).** The first Mem0
  v2 eval scored answerable **0.138** — a 4× collapse, not a plausible effect of
  the date fix. Root cause: Mem0's async extraction plateaus in bursts, and the
  ingest settle loop (stable-for-20s) returned during an early plateau while the
  store was still nearly empty (~15 memories for a conv that settled at ~250), so
  eval ran against starved retrieval and abstained on 86% of answerable
  questions. The stores were correctly ingested and settled to healthy counts
  (v2 total 2,157 memories, conv0=174 vs v1 176). Fix: the settle loop now
  requires a long stable window (120s) AND a minimum elapsed floor (180s); the
  invalid output is archived at
  `docs/runs/local/locomo_e2e_mem0_v2_PREMATURE_0.138.jsonl`, and the arm was
  re-evaluated on the full stores (mem0_has reuses them, no re-ingest).
  Re-eval answerable recovered to a healthy ~0.5+ range (NO-ANSWER on answerable
  fell from 86% to ~13%). Publishing the 0.138 would have been a false, huge win
  for Provem — exactly the kind of artifact this campaign exists to prevent.

- **Mem0 search quota exhausted (blocker).** After the premature-eval waste
  (~1,986 searches on the sparse store) plus the partial re-eval and the v1
  campaign, the Mem0 account hit its monthly cap: `event_type SEARCH,
  quota_limit 5000, quota_used 5000, quota_reset 2026-08-30`. No further Mem0
  searches are possible until the reset. The v2 Mem0 arm therefore has 374
  valid full-store rows and is otherwise incomplete; the ingested stores
  persist (2,157 memories) so the eval can be finished with ~1,986 searches
  once quota resets (or immediately on a paid plan). The Zep arm is unaffected
  (separate credits) and completes.
  **Resolution (2026-08-02):** the maintainer enabled Mem0 overage ($5/1,000
  calls), so the Mem0 v2 arm WAS completed the same day — 1,986 rows, 0 errors,
  full stores. The 374-rows-incomplete state above is the mid-run snapshot when
  the quota first blocked; the published v2 Mem0 numbers below are the complete
  1,540-answerable measurement, not the partial one.

### v2 results (2026-08-02) — corrected Mem0 + Zep vs frozen Provem

Both baseline arms re-measured on the corrected pipeline (Mem0 single-date,
full stores; Zep chronological order, full graph 5,882 episodes). Provem is the
frozen `iter/c2_full_final2.jsonl`. Source of every number:
`docs/runs/three_system_report_v2.json` (regenerated by
`scripts/three_system_report.py`, deterministic, EUR 0).

**Scoreboard — answerable accuracy, n=1,540 (v1 → v2):**

| Judge | Provem | Mem0 v1 → v2 | Zep v1 → v2 |
|---|---|---|---|
| Strict binary (gpt-5) | 0.614 | 0.509 → **0.565** | 0.449 → **0.449** |
| Cross-vendor (claude-opus-5) | 0.502 | 0.419 → **0.368** | 0.329 → **0.304** |
| Mem0's own judge prompt | 0.772 | 0.722 → **0.716** | 0.632 → **0.649** |
| Abstention (n=446) | 0.863 | 0.848 → **0.830** | 0.704 → **0.722** |

**What changed and why:**
- **Mem0's double-date bug was hurting Mem0.** Fixing it (single date prefix +
  full-store retrieval) raised strict-judge Mem0 from 0.509 to **0.565** and
  holdout from 0.503 to **0.582**. The Provem→Mem0 strict lead therefore
  narrows from +10.5 pts to **+4.9 pts** (McNemar 249/173, p = 2.5×10⁻⁴ — still
  significant, roughly half the previously-published margin).
- **Under the opus judge, corrected Mem0 scores lower (0.419 → 0.368).** The v2
  Mem0 answers more questions (fewer abstentions), and opus rejects more of
  those additional borderline answers than the strict gpt-5 judge accepts —
  strict-vs-opus κ for Mem0 falls from 0.76 to 0.54. Reported as-is; it is a
  real judge-divergence signal, not smoothed over.
- **Zep was essentially unaffected by its fixes**: strict 0.449 → 0.449 (691 vs
  692 correct, a one-question difference), so the lexical-order bug did not move
  Zep's aggregate — though temporal stays its weakest category (0.290).
- **Ordering Provem > Mem0 > Zep is invariant under all three judges.** All
  pairwise answerable differences are significant except abstention
  Provem-vs-Mem0 (47/32, p = 0.115, a tie — as in v1). Per category (strict,
  corrected labels): with a fairly-measured Mem0, Provem now leads all four —
  multi-hop 0.411/0.394/0.330, temporal 0.614/0.523/0.290, single-hop
  0.717/0.672/0.566, open-domain 0.312/0.271/0.312.

**Accounting:** Mem0 store counts healthy (2,157 memories total). Zep ingestion
198 credits for conv 0, all 10 convs bulk-ingested and graph-processed in
parallel (per-account parallelism confirmed; aggregate ~21 episodes/min with 3
graphs active). Mem0 search quota (5,000/mo) was exhausted by the premature-eval
waste; the maintainer enabled overage ($5/1,000) to finish. remeasure_ledger
tracks the OpenAI answerer/judge spend (~€8 across the arms; the m0j gpt-5 judge
cost is billed but under-counted in the run log due to separate module
instances — ~€5 real). 4,287 judge top-up calls (opus + m0j) across both v2
arms; Provem's opus verdicts reused from the frozen `ours_opt` set at EUR 0.

**Publication:** the v2 numbers replace v1 in README and
`docs/three_system_benchmark.md`; the v1 numbers are retained here as the
historical record with the bug root-causes above.
