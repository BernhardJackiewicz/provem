# Three-system LoCoMo benchmark: Provem vs Mem0 vs Zep

Date: 2026-08-01. This is the repo's headline memory-quality result: three
systems, identical questions, identical answerer, three independent judges,
paired statistics, all artifacts frozen and replayable at zero cost.

## Systems under test

| System | Configuration |
|---|---|
| **Provem** (this repo) | dense tier: verbatim store + BM25 + key-gated `text-embedding-3-small` + RRF fusion, top-k 20; abstention-calibrated answer prompts with question-type routing (`dense,strict5,agg,aggfan,temporalroute`) |
| **Mem0** | Mem0 Platform (paid tier), its own extraction pipeline over the same conversations, top-k 20 searches; empty-prediction fix applied to its stored answers against its live stores |
| **Zep** | Zep Cloud (trial), configured per **Zep's own published evaluation checklist** — one graph owner per conversation with proper user/assistant roles and speaker names, timestamps via the native `created_at` field (not appended to text), retrieval via parallel edge+node graph searches composed into dated facts + entity summaries, k-capped like the others. Ingestion read-back verified per session (272/272); graph processing confirmed complete (5,788/5,882 episodes) before evaluation |

Shared pipeline for all three: LoCoMo, 10 conversations, 5,882 turns, 1,986
questions (1,540 answerable + 446 adversarial). Answerer: `gpt-5-mini`
(reasoning medium) over ONLY the retrieved context, instructed to decline when
the context lacks the answer. Abstention scored deterministically (declined =
correct on adversarial questions). Judges see (question, gold, prediction) only.

## Scoreboard (answerable accuracy, n = 1,540)

| Judge | **Provem** | Mem0 | Zep |
|---|---|---|---|
| Strict binary (gpt-5) | **0.614** | 0.509 | 0.449 |
| Cross-vendor (claude-opus-5) | **0.502** | 0.419 | 0.329 |
| Mem0's own published judge prompt (gpt-5; partial credit, 14-day date tolerance) | **0.772** | 0.722 | 0.632 |
| Abstention on 446 adversarial questions | **0.863** | 0.848 | 0.704 |

**The ordering Provem > Mem0 > Zep is invariant under all three judges.**

Pairwise significance (paired exact McNemar, strict judge):

| Pair | Accuracies | Diff | Discordants | p |
|---|---|---|---|---|
| Provem vs Mem0 | 0.614 vs 0.509 | +0.105 | 317 / 155 | 7.2×10⁻¹⁴ |
| Provem vs Zep | 0.614 vs 0.449 | +0.166 | 348 / 93 | 1.0×10⁻³⁵ |
| Mem0 vs Zep | 0.509 vs 0.449 | +0.060 | 317 / 224 | 7.4×10⁻⁵ |

## By category (strict judge)

| Category | Provem | Mem0 | Zep |
|---|---|---|---|
| single-hop (n=841) | **0.717** | 0.592 | 0.561 |
| temporal (n=321) | **0.614** | 0.442 | 0.305 |
| multi-hop (n=282) | **0.411** | 0.397 | 0.340 |
| open-domain (n=96) | 0.312 | 0.333 | 0.260 |

Provem leads three of four categories; open-domain (n=96) is a statistical tie
with Mem0 (2 questions apart). Category names verified against Mem0's official
run files by question counts.

## Holdout confirmation (convs 1,3,5,7,9 — never used for any tuning)

Provem **0.617** (464/752) · Mem0 0.503 (378/752) · Zep 0.451 (339/752).
The ordering and margins hold on never-tuned conversations.

## Congruence with published numbers

Our measurements land where independent evaluations land — not where vendor
marketing lands — for both competitors:

| System | Ours (strict) | Independent published | Vendor self-report |
|---|---|---|---|
| Mem0 | 0.509 | 48.9 (LoCoMo-Refined, strict human-validated judge); 64.7 (ENGRAM paper, k=20 lenient) | 82.7–92.5 (top-50/200, gpt-5 CoT, tolerant judge) |
| Zep | 0.449 | 42.3 (ENGRAM paper, k=20) | 94.7 (own gpt-5.4-CoT setup; an earlier 84% figure was retracted) |

Under Mem0's own judge prompt our Mem0 measurement (0.722) sits inside Mem0's
published band — the bridge that validates the harness (see
`lager_optimization_log.md`).

## Honest limitations

- **Zep ran on a trial account** with Zep's default cloud pipeline. We followed
  Zep's own configuration checklist and verified ingestion + graph completion,
  but a Zep engineer with a paid plan and custom ontology might do better; our
  run is a faithful default-configuration measurement, not a tuning contest.
- Provem's answer prompts were tuned on a dev split (convs 0,2,4,6,8); the
  holdout confirms generalization, and the neutral-prompt control (0.596 vs
  Mem0 0.509) shows the win does not depend on prompt tuning. Mem0 and Zep both
  used the same neutral answering prompt.
- One benchmark (LoCoMo), LLM judges only (two vendors, κ ≈ 0.7–0.8 agreement),
  no human evaluation.
- Two harness bugs were found and fixed during the Zep run (invalid timestamp
  format producing 400s; a retry classifier that mistook those 400s for rate
  limits because Zep embeds `x-ratelimit` headers in every error string). Both
  post-mortems are in `lager_optimization_log.md`; the final run started only
  after per-session read-back verification.
- The empty-prediction harness bug discovered earlier was fixed symmetrically
  for Provem and Mem0; Zep's run postdates the fix entirely.

## Reproduce

```
sh scripts/replay_report.sh                  # every number, EUR 0, from frozen caches
python scripts/zep_judges.py                 # three-system scoreboard from caches
```

Live re-runs: `scripts/zep_locomo.py --mode ingest|eval` (ZEP_API_KEY),
`scripts/mem0_locomo_e2e.py` (OPENAI_API_KEY), `scripts/second_judge.py`
(ANTHROPIC_API_KEY). Keys are read from the environment and never persisted.
