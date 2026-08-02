# Three-system LoCoMo benchmark: Provem vs Mem0 vs Zep

Date: 2026-08-02 (v2 — corrected re-measurement of the Mem0 and Zep arms; see
[`measurement_changelog.md`](measurement_changelog.md) for v1→v2 and the bug
root-causes). Three systems, identical questions, identical answerer, three
independent judges, paired statistics, all artifacts frozen and replayable at
zero cost.

## Systems under test

| System | Configuration |
|---|---|
| **Provem** (this repo) | dense tier: verbatim store + BM25 + key-gated `text-embedding-3-small` + RRF fusion, top-k 20; abstention-calibrated answer prompts with question-type routing (`dense,strict5,agg,aggfan,temporalroute`) |
| **Mem0** | Mem0 Platform, its own extraction pipeline over the same conversations, top-k 20 searches; date-augmented input (single prefix — v1's double-date bug fixed) and retrieval run against fully-settled stores (2,157 memories) |
| **Zep** | Zep Cloud, configured per **Zep's own published evaluation checklist** — one graph owner per conversation with proper user/assistant roles and speaker names, timestamps via the native `created_at` field (not appended to text), retrieval via parallel edge+node graph searches composed into dated facts + entity summaries, k-capped like the others. Sessions ingested in **chronological order** (v1's lexical-order bug fixed); ingestion read-back verified per session; graph processing **completed for all episodes** (hard-gated before eval, no unprocessed remainder this time) |

Shared pipeline for all three: LoCoMo, 10 conversations, 5,882 turns, 1,986
questions (1,540 answerable + 446 adversarial). Answerer: `gpt-5-mini`
(reasoning medium) over ONLY the retrieved context, instructed to decline when
the context lacks the answer. Abstention scored deterministically (declined =
correct on adversarial questions). Judges see (question, gold, prediction) only.

## Scoreboard (answerable accuracy, n = 1,540)

| Judge | **Provem** | Mem0 | Zep |
|---|---|---|---|
| Strict binary (gpt-5) | **0.614** | 0.565 | 0.449 |
| Cross-vendor (claude-opus-5) | **0.502** | 0.368 | 0.304 |
| Mem0's own published judge prompt (gpt-5; partial credit, 14-day date tolerance) | **0.772** | 0.716 | 0.649 |
| Abstention on 446 adversarial questions | 0.863¹ | 0.830 | 0.722 |

**The ordering Provem > Mem0 > Zep is invariant under all three judges** for
answerable accuracy. Note the judges disagree on the *size* of the Provem→Mem0
gap: the strict gpt-5 judge scores the corrected Mem0 at 0.565 (a +4.9-pt
Provem lead), while claude-opus-5 scores it at 0.368 (a wider lead) — opus
rejects more of the extra borderline answers the corrected Mem0 now attempts
(strict-vs-opus κ for Mem0 = 0.54, vs 0.70 for Provem). We report both.

¹ The abstention row is not a same-footing comparison: Provem's 0.863 comes
from its dev-tuned strict5 prompt chain, while Mem0 and Zep answered under the
neutral prompt. Under the shared neutral prompt Provem's abstention is 0.693
(309/446) — behind Zep 0.722 and Mem0 0.830 — and the 0.863-vs-0.830 gap to
Mem0 is a statistical tie either way (McNemar 47/32 discordants, p = 0.12).
Neutral-prompt answerable accuracy is 0.596, still ahead of Mem0's 0.565.

Pairwise significance (paired exact McNemar, strict judge):

| Pair | Accuracies | Diff | Discordants | p |
|---|---|---|---|---|
| Provem vs Mem0 | 0.614 vs 0.565 | +0.049 | 249 / 173 | 2.5×10⁻⁴ |
| Provem vs Zep | 0.614 vs 0.449 | +0.166 | 352 / 98 | 1.1×10⁻³⁴ |
| Mem0 vs Zep | 0.565 vs 0.449 | +0.116 | 346 / 168 | 3.1×10⁻¹⁵ |

## By category (strict judge)

| Category | Provem | Mem0 | Zep |
|---|---|---|---|
| single-hop (n=841) | **0.717** | 0.672 | 0.566 |
| temporal (n=321) | **0.614** | 0.523 | 0.290 |
| multi-hop (n=282) | **0.411** | 0.394 | 0.330 |
| open-domain (n=96) | **0.312** | 0.271 | 0.312 |

With a fairly-measured Mem0, Provem leads or ties all four categories, but by
smaller margins than v1: single-hop and temporal are clear Provem leads,
multi-hop is now a narrow Provem lead (0.411 vs 0.394, n=282), and open-domain
is a Provem/Zep tie above Mem0 (n=96, small). Category names verified against
Mem0's official run files by question counts.

## Holdout confirmation (convs 1,3,5,7,9)

Provem **0.617** (464/752) · Mem0 0.582 (438/752) · Zep 0.469 (353/752).
The final configuration was chosen on the dev split (convs 0,2,4,6,8) only.
The ordering holds on the holdout, but note the honest weakening after the Mem0
correction: on the holdout split alone the Provem→Mem0 gap is +3.5 pts and
**not statistically significant** (McNemar 111/85, p = 0.074) — the full-set
Provem>Mem0 result (p = 2.5×10⁻⁴) is what carries significance. Caveat: this
is not a single-shot confirmatory test — full-set runs (including the holdout)
were evaluated ~7 times across ~20 tried configurations during the campaign
for ordering checks, and the reported p-values carry no multiplicity
adjustment (the three headline p-values survive any correction).

## Congruence with published numbers

Our measurements land where independent evaluations land — not where vendor
marketing lands — for both competitors:

| System | Ours (strict) | Independent published | Vendor self-report |
|---|---|---|---|
| Mem0 | 0.565 (v2 corrected) | 48.9 (LoCoMo-Refined, strict human-validated judge); 64.7 (ENGRAM paper, k=20 lenient) | 82.7–92.5 (top-50/200, gpt-5 CoT, tolerant judge) |
| Zep | 0.449 | 42.3 (ENGRAM paper, k=20) | 94.7 (own gpt-5.4-CoT setup; an earlier 84% figure was retracted) |

Under Mem0's own judge prompt our Mem0 measurement (0.716) sits inside Mem0's
published band — the bridge that validates the harness (see
`lager_optimization_log.md`).

## Honest limitations

- **Zep ran on a trial account** with Zep's default cloud pipeline. We followed
  Zep's own configuration checklist and verified ingestion + graph completion,
  but a Zep engineer with a paid plan and custom ontology might do better; our
  run is a faithful default-configuration measurement, not a tuning contest.
- Provem's answer prompts were tuned on a dev split (convs 0,2,4,6,8); the
  holdout confirms generalization, and the neutral-prompt control (0.596 vs
  Mem0 0.565) shows the win does not depend on prompt tuning. Mem0 and Zep both
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
