# End-to-end LoCoMo: our memory vs Mem0 — the definitive comparison

> **FINAL UPDATE (2026-07-31, campaign 2): after fixing an empty-prediction harness
> bug SYMMETRICALLY on both sides and closing the multi-hop gap, the final paired
> result is **0.614 vs 0.509 (+10.5 pts, p=7.2e-14)** under gpt-5 and **0.502 vs
> 0.419 (+8.2, p=5.1e-10)** under the independent claude-opus-5 judge; abstention
> parity 0.863 vs 0.848; multi-hop flipped (0.411 vs 0.397). See `ship_report.md`
> for the full scoreboard and honest limitations.**
>
> **UPDATE (2026-07-31): after an iterative, failure-driven optimization campaign
> (see `lager_optimization_log.md`), OUR memory now beats Mem0 on this same
> benchmark: answerable 0.536 vs 0.475 (+6.2 pts, McNemar p = 4.2×10⁻⁶),
> abstention parity (0.886 vs 0.883), confirmed on never-tuned holdout
> conversations (+6.8 pts, p = 4.2×10⁻⁴). Key change: key-gated dense-embedding
> + RRF hybrid retrieval plus an abstention-calibrated answerer prompt. The
> section below documents the ORIGINAL baseline comparison (our 0.388), kept
> intact as the honest starting point.**

**Question:** which memory is stronger, and by exactly how much?

**Answer (original baseline, 2026-07-30):** on the correct, literature-standard end-to-end benchmark (retrieve →
LLM answers → LLM judges), **Mem0's memory is stronger than ours.** On answerable
LoCoMo questions Mem0 scores **0.475 vs our 0.388** — a **+8.7-point** gap
(95% CI 5.8–11.6), **statistically significant** (paired McNemar p ≈ 5.2×10⁻⁹).
In relative terms Mem0 gets **~22% more answers right** (0.475 / 0.388 = 1.22×).
Mem0 leads in **every category** and in **9 of 10 conversations**.

This **reverses** the earlier retrieval-recall snapshot (`mem0_memory_benchmark.md`),
where our verbatim store looked comparable-or-ahead. That metric rewarded raw
answer-token presence, which verbatim storage gets for free. The moment a real LLM
has to *produce* an answer, Mem0's clean, distilled memories beat our verbose raw
turns. This is Mem0's design thesis, and the end-to-end test confirms it — and it
matches Mem0's published LoCoMo range.

## Setup (identical for both — only the memory differs)

- **Dataset:** full LoCoMo, all 10 conversations, 1986 QA (1540 answerable + 446
  adversarial/no-info). 5882 dialogue turns.
- **Ingestion:** each conversation ingested into each memory, date-augmented in
  LoCoMo answer format (`"7 May 2023 | Speaker: ..."`) identically for both. Mem0
  distilled the 5882 turns to ~1700 extracted memories (≈150–250 per conversation);
  our side keeps turns verbatim + lexical recall-boost retrieval.
- **Retrieval:** top-k = 20 from each memory.
- **Answerer (shared):** `gpt-5-mini`, `reasoning_effort=medium`, answering from
  ONLY the retrieved context, instructed to say `NO ANSWER` when absent.
- **Judge (shared):** `gpt-5`, `reasoning_effort=low`, semantic correctness
  (paraphrase- and date/number-format-robust), YES/NO.
- **Abstention:** answerable questions are judged for correctness; adversarial/
  no-info questions are scored correct iff the system declined (`NO ANSWER`).
- **Cost:** €3.81 total OpenAI (7097 LLM calls); well under the €30 cap. Mem0 paid
  tier (the full 10-conversation ingestion exhausted one billing period's quota
  and had to be topped up before the query phase).

## Results

### Headline — answerable accuracy (n = 1540)

| System | Accuracy | 95% CI | Correct |
|---|---|---|---|
| Mem0 | **0.475** | [0.450, 0.500] | 731 |
| Ours | **0.388** | [0.364, 0.412] | 597 |

- **Difference (ours − Mem0): −0.087**, 95% CI **[−0.116, −0.058]** (paired bootstrap).
- **Paired McNemar:** ours-only-correct = 195, Mem0-only-correct = 329,
  discordant = 524, **p ≈ 5.2×10⁻⁹** → the gap is real, not noise.
- **Relative:** Mem0 answers ~**22% more** questions correctly.

### Abstention — declining trick questions (n = 446)

| System | Accuracy | Correct |
|---|---|---|
| Mem0 | 0.883 | 394 |
| Ours | 0.863 | 385 |

Difference −0.020, 95% CI [−0.058, +0.018], McNemar **p = 0.34 → a tie.** Both
memories decline unanswerable questions about equally well (governance parity).

### By category (answerable)

| Category | Ours | Mem0 | Mem0 lead |
|---|---|---|---|
| multi-hop (cat 1) | 0.206 | 0.316 | +0.110 |
| temporal (cat 2) | 0.411 | 0.436 | +0.025 |
| open-domain (cat 3) | 0.198 | 0.260 | +0.062 |
| single-hop (cat 4) | 0.461 | 0.567 | +0.106 |

Mem0 leads everywhere; the gap is widest on **multi-hop** and **single-hop**,
where distilled memories that connect facts across sessions help the answerer
most. (Historical note: this table originally carried the harness's
pre-correction category names, which mislabeled codes 2/3/4. Labels were
corrected post-hoc to the official LoCoMo mapping — 1=multi-hop, 2=temporal,
3=open-domain, 4=single-hop; the per-code values are unchanged.)

### By conversation

Mem0 wins **9 of 10**; we win only conv-1 (0.506 vs 0.444). Per-conv accuracy
ranges 0.33–0.51 (ours) vs 0.43–0.53 (Mem0).

## Honest reading

- **Our Lager is genuinely behind Mem0 on the standard benchmark.** This is
  consistent with everything measured before: our retrieval recall was the
  bottleneck, and a strong answerer cannot recover answers our retrieval buries
  in noise as well as it can read Mem0's curated memories.
- **Config sensitivity (important caveat).** End-to-end accuracy is very sensitive
  to answerer strength. With a *weak* answerer (`reasoning_effort=minimal`) our
  side collapsed to 0.20 while Mem0 held ~0.60 — because our verbose verbatim
  context needs reasoning to parse. At a fair `medium` answerer the gap narrows to
  the 8.7 points reported here. We report the fair setting; a weaker or stronger
  answerer would move both numbers (and the gap).
- **Where we are NOT behind:** declining unanswerable questions is a tie, and the
  governance/reliability layer (the "Schloss": injection quarantine, source-trust
  conflict resolution, tenant erasure, calibrated abstention) is a different axis
  this benchmark does not test — that is where our value sits, not raw recall.
- **What would close the recall gap:** better retrieval (dense/hybrid embeddings,
  reranking) and/or an extraction/consolidation step so the answerer reads
  distilled facts instead of raw turns — i.e. adopting the part of Mem0's design
  that this result vindicates.

## Threats to validity

- Single answerer/judge family (gpt-5-mini / gpt-5). A different judge could shift
  absolute numbers; the large, significant gap is unlikely to flip.
- Mem0's async indexing is slow; stores were settled (~1700 memories) before the
  query phase, but Mem0 keeps distilling for a long time after ingest.
- LLM-judge grading has irreducible noise on borderline paraphrases; applied
  identically to both, so it does not bias the *difference*.

## Reproduce

```
export OPENAI_API_KEY=... MEM0_API_KEY=...
# ingest is slow-async on Mem0 and can exhaust a billing period's quota for the
# full 10 conversations; the harness reuses existing stores and resumes.
python scripts/mem0_locomo_e2e.py --workers 4 --include-abstain \
    --answer-effort medium --judge-effort low --out runs/e2e.jsonl
python scripts/mem0_e2e_report.py --in runs/e2e.jsonl
```

Per-QA predictions + verdicts: `docs/runs/locomo_e2e_ours_vs_mem0.jsonl`.
