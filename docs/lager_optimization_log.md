# Lager optimization campaign log — beating Mem0 on LoCoMo E2E

> Category names throughout this log were corrected post-hoc (see the
> 2026-07-31 correction note below): cat2=temporal, cat3=open-domain,
> cat4=single-hop. All per-code numbers are unchanged.

**Goal:** answerable accuracy > Mem0's frozen 0.475 on all 1540 LoCoMo QA, significantly
(target ≥ ~0.505, paired McNemar p < 0.05), with zero regressions in the Wächter
(reliability headline, quality gate, abstention ≥ 0.85, full test suite).
**Budget:** €30 OpenAI hard cap (ledger-enforced). **Method:** iterate on DEV
(convs 0,2,4,6,8; n=788 answerable), holdout convs 1,3,5,7,9; promote a change only at
≥ +1.5 pts DEV; full-set checkpoint per wave; final claim only on all 1540 paired
against the frozen Mem0 rows of `docs/runs/locomo_e2e_ours_vs_mem0.jsonl`.

Evidence base for the interventions: failure analysis over the definitive run
(49% of losses to Mem0 are retrieval misses; patterns: 30% paraphrase/attribute
colocation, 15% answerer-fail in verbose context, 14% relative-date resolution,
13% coreference/adjacency, 12% literal-vs-inference, 9% aggregation) and a win-taxonomy
(verbatim precision + niche-fact coverage + no date mangling — must be preserved).

| Iter | Change (features) | DEV answerable | Δ DEV | DEV abstain | Full-set | Gates | € (run / total) | Promoted |
|---|---|---|---|---|---|---|---|---|
| 0 | Infra: ours-only mode, LLM caches (seeded from baseline), feature flags, ledger, paired report vs frozen Mem0 | 0.391 (308/788) | — (baseline) | 0.862 | 0.388 (from definitive run) | all green | 0.00 / 0.00 | — |
| 1 | `window` (±1 adjacency turns) — PARTIAL (75%, OpenAI account quota ran dry mid-run) | 0.411 vs 0.384 same-slice (n=601) | **+2.7** | **0.796 (−7.9) → GATE FAIL** | — | abstention gate FAIL | ~1.43 / ~1.43 | **no** |
| 2 | `window_prev` (preceding turn only) | 0.395 (311/788) | +0.4 | 0.858 ✓ | — | abstain ok, lift below threshold | 1.02 / 2.45 | **no** |
| 3 | `temporal` (write-time date annotation) | 0.391 (308/788) | ±0.0 | 0.845 (−1.7) | — | neutral | 0.65 / 3.10 | **no** |
| 4 | `window_next` (following turn only) | 0.406 (320/788) | +1.5 | **0.793 → GATE FAIL** | — | abstention gate FAIL | 1.02 / 4.12 | **no** |
| 5 | `dense` (embeddings + RRF hybrid) | **0.519 (409/788)** | **+12.8** | **0.797 → GATE FAIL** | — | huge lift, abstention broken | 0.96 / 5.08 | not yet (strict combo pending) |
| 6 | `dense,strict` | 0.423 | +3.2 | 0.888 ✓ | — | strict1 too harsh: eats 9.6 of dense's 12.8 pts | 0.93 / 6.00 | no |
| 7 | `dense,strict2` (fabrication-only ban, inference allowed) | **0.543** | **+15.2** | 0.823 ✗ | — | answerable even above dense-alone; abstain 3 flips short | 0.99 / 6.99 | no |
| 8 | `dense,strict3` (+ no premise-correction) | 0.539 | +14.8 | 0.841 ✗ (= Mem0 parity) | full checkpoint running | still 3 flips short of 0.85 | 0.98 / 7.97 | no |
| 9 | `dense,strict4` (+ infer-only-what's-implied) | **0.534** | **+14.3** | **0.866 ✓ (above baseline!)** | pending | **ALL DEV GATES GREEN** | 0.96 / 8.94 | **candidate** |
| F1 | full-set checkpoint `dense,strict3` | — | — | — | 0.523 / abst 0.854 ✓ / p=3.9e-4 | green | 1.03 / 9.00 | superseded by F2 |
| F2 | **FINAL full-set `dense,strict4`** | — | — | — | **0.536 / abst 0.886 / p=4.2e-6** | **all green** | 0.96 / 10.92 | **PROMOTED** |

## CAMPAIGN RESULT (2026-07-31): target reached — significantly better than Mem0

Final configuration `dense,strict4` on all 1540 answerable + 446 adversarial QA, paired
against Mem0's frozen rows (identical questions, answerer model, judge):

- **Answerable: OURS 0.536 [0.511, 0.561] vs MEM0 0.475 [0.450, 0.500]** —
  diff **+0.062** (95% CI +0.036..+0.088), McNemar ours-only-right 258 vs 163,
  **p = 4.2×10⁻⁶** → significantly better. Relative: **+13%** more correct answers.
- **Abstention: OURS 0.886 vs MEM0 0.883** (p = 1) — exact parity, and **above our own
  0.863 baseline** → no Wächter regression; the abstention-calibration iterations paid off.
- **Holdout-only confirmation** (convs 1,3,5,7,9 — never used for tuning): answerable
  **0.539 vs 0.471 (+0.068, p = 4.2×10⁻⁴)**, abstention 0.907 ✓ → the win generalizes;
  not a DEV-overfitting artifact.
- Categories: temporal 0.520 vs 0.436, single-hop 0.667 vs 0.567, open-domain 0.302 vs
  0.260; multi-hop remains Mem0's (0.245 vs 0.316) — honest residual gap (cross-session
  assembly needs extraction/consolidation, wave 3, not required for the target).
- **Wächter gates all green** at promotion: 488 unit tests OK, reliability headline exactly
  stable (governed 0.900, 0 violations, benign accuracy 1.0), quality-gate PASS (incl.
  external_reliability), MCP-sqlite restart smoke PASS, feature-less baseline byte-identical.
- **Cost: €10.92 of €30** (ledger-verified).

What made the difference (in causal order of the campaign):
1. **Failure-driven design**: 49% of losses to Mem0 were retrieval misses; the largest
   single pattern (30%) was the paraphrase/attribute-colocation gap.
2. **`dense`** — key-gated text-embedding-3-small + RRF fusion over the lexical top-50:
   +12.8 pts DEV alone. This is the fix for exactly that pattern.
3. **`strict2→4` prompt calibration** — restored abstention (0.797 → 0.886) that richer
   contexts had eroded, at a cost of only ~0.9 pts answerable, via flip-analysis-driven
   wording: ban fabrication (not inference) → never correct false premises → infer only
   what is directly implied.

## Next frontiers (hypothesis catalog, 2026-07-31 — designed, not yet executed)

Mining the FINAL config's 38 multi-hop losses reframed footnote 1: **it is ~2/3 an
enumeration problem, not a reasoning problem** (13/38 have the full answer in context but
strict4 suppresses lists — "Figurines" instead of "Figurines, shoes"; 24/38 need exactly
2 evidence turns; the rest split 11 partial / 14 retrieval-miss).

**Multi-hop (0.245 → target >0.316):** H-M1 aggregation-routed prompt variant
(regex-router → strict4-agg that licenses enumeration of explicitly stated items;
~€1.5, +4-6 pts multi-hop). H-M2 per-session diversity quota + dense-MMR for routed
questions (~€1, +1.5-2.5). H-M3 deterministic per-entity sub-query union for "both X
and Y" (~€1, +1.5-2.5). Together realistically +7-10 pts → parity to slightly above
Mem0. H-M4 wave-3 fact extraction + governed cross-session rollups (~€6, 2.5d,
+5-8 pts standalone) is the structural fix and the only one that scales past parity;
highest risk (extraction poisoning → substring-filter + provenance + review queue).

**Keyless tier (0.39 → honest ceiling ~0.42-0.44):** H-K1 window_next×strict4 (never
measured together; ~€2, +0.5-1.5). H-K2 corpus-internal PPMI co-occurrence query
expansion (stdlib; ~€2.5, +1-3). H-K3 RM3-style pseudo-relevance feedback (~€1.5,
+0.5-2). **Verdict: keyless parity with Mem0 is NOT realistic** — the +12.8-pt lever was
semantic embeddings; corpus statistics recover only a fraction. H-K4 optional
`[local-embeddings]` pip extra (static/ONNX model, no API key) reaches ~0.47-0.50 — the
honest middle tier between stdlib-default and key-gated dense.

**External validity ("one benchmark, one judge"):** V1 cross-vendor second judge over the
STORED predictions of both systems (~€1.5, no retrieval needed) — kills the
"answerer-vendor judges itself" objection. V2 full-set neutral-prompt symmetric control
(ours dense/pv=v1 vs frozen neutral Mem0; ~€1) — removes the strict4-asymmetry caveat
(DEV already suggests we still win: 0.519 vs 0.478). V3 zero-cost reproducibility freeze
(run manifest + caches + €0 replay target). V4 LongMemEval-S port (~€4.5, 1.5d,
ours-vs-ourselves ablation; no affordable Mem0 arm there — quota). V1+V2+V3 ≈ €2.5 and
upgrade the claim to "two judge vendors, prompt-symmetric, bit-exact reproducible".

## Campaign 2 (2026-07-31): multi-hop, validity hardening, ship report

### The empty-prediction harness bug (discovered via M1 flip analysis)
M1 (aggregation-routed enumeration prompt) moved DEV multi-hop ±0.0 — investigating WHY
exposed a real harness bug: reasoning models can burn the entire completion budget
thinking and return **empty content with finish_reason=length (HTTP 200)**, which the
harness silently scored as a wrong answer on answerable questions. Counts on the frozen
runs: **ours-optimized 352/1540 (22.9%) answerable predictions empty, Mem0 195/1540
(12.7%), ours-baseline 251/1540 (16.3%)** — it suppressed BOTH systems, ours more
(verbose contexts + strict prompts → more reasoning). Fix: retry with doubled budget on
empty+length; stale cached empties re-asked with headroom (cache overwrite, last-line-wins).
**Fairness: the fix is applied to BOTH sides** — our side re-run locally; Mem0's side
re-asked against its still-existing stores through the identical pipeline
(`scripts/patch_mem0_empties.py`, neutral prompt as in its original run). Caveat
documented: Mem0's stores kept distilling since the freeze (drift favors Mem0 →
conservative for us). All prior numbers in this log predate the fix and are labeled
by their run files; the post-fix comparison supersedes them.
- M1 router audit (free, offline): matches 18/38 mined multi-hop losses, 11.8% of
  answerable, 7.6% of adversarial questions (premise-ban retained as their guard).
- M1 alone on DEV (pre-fix): multi-hop ±0.0 (+3/−3), overall −0.3, abstention +0.5 —
  the enumeration license is useless while list items are missing from context AND
  while empty-content swallows the enumerating answers. Re-evaluated post-fix.

### Post-fix picture (both sides repaired symmetrically)
- **OURS(fixed) 0.611 vs MEM0(patched) 0.509 answerable (+10.2 pts, p=1.3e-13)** — the
  bug had hidden ~7.5 pts of our true accuracy (and ~3.4 of Mem0's: 731→784, 53 of 195
  re-asked empties now correct). Multi-hop gap shrinks to −2.5 (0.372 vs 0.397);
  open-domain now clearly ours (0.406 vs 0.333); temporal 0.629 vs 0.442.
- **BUT abstention collapsed to 0.780** (Mem0 0.848): empty answers on adversarial
  questions had counted as declines — the strict4 calibration partly rode on the bug.
  Honest and expected: fixing an artifact exposes the real calibration state.
- **Cross-vendor second judge (claude-opus-5, pre-fix predictions): both findings
  CONFIRMED.** Baseline: ours 0.347 vs mem0 0.404 (p=6.9e-5, Mem0 ahead — same sign as
  gpt-5). Optimized: ours 0.478 vs mem0 0.404 (**+7.4 pts, p=3.1e-8** — same sign,
  even larger). Opus 5 is stricter in absolute terms; the paired differences are what
  survives. Judge agreement κ=0.73–0.83. Cost €2.9–8.8 (pricing-band).
- **strict5** (mention-check hardened: "similar-topic content is NOT enough") on DEV:
  answerable 0.608 (no loss vs 0.611-slice), **abstention 0.853 ✓ gate recovered**,
  multi-hop 0.384. Promotion candidate; full-set run in flight.

### Campaign 2 finals (2026-07-31): every gap closed or statistically tied
Iteration attribution on DEV (each €0.3-1.8, caches make unchanged questions free):
- V2 neutral-prompt control (full set): **ours 0.596 vs Mem0 0.509 even with the
  completely untuned prompt** — the win does not depend on prompt tuning; the strict
  chain only buys abstention (0.693 neutral → 0.863 strict5).
- agg-prompt license alone: ±0.0 (post-fix the enumeration flows anyway). Session-
  diversity/MMR: −2.0 multi-hop (displaces useful context) — REJECTED. **Sub-query
  fanout (per-entity split + operator strip, RRF-unioned): +1.3 multi-hop, abstention
  unchanged — PROMOTED.** Temporal-typed questions route to strict4 (strict5's mention
  check cost date answers; only 3.4% of adversarials are temporal-typed): +0.4 overall.
- **FINAL config `dense,strict5,agg,aggfan,temporalroute`, full 1986 QA, paired vs
  Mem0-patched:**
  - answerable **0.614 vs 0.509 (+10.5 pts, McNemar p=7.2e-14)**; holdout-only
    **0.617 vs 0.503 (+11.4, p=1.7e-8)**
  - abstention **0.863 vs 0.848** (gate ≥0.85 ✓, parity p=0.49)
  - by category: multi-hop **0.411 vs 0.397** (flipped!), temporal 0.614 vs 0.442,
    single-hop 0.717 vs 0.592, open-domain 0.312 vs 0.333 (2 questions of 96 — statistical
    noise, CIs overlap massively; further iteration on it would be noise-chasing).
- Wächter gates at close: 490 tests OK, reliability headline exact (governed 0.900,
  0 violations, benign 1.0), quality-gate 10× PASS, feature-less default byte-identical.
- Ledger: OpenAI €19.04/€30; Anthropic ~€3-9/€20 (pricing band), second-judge top-up on
  final predictions in flight.

Honest limitations (documented for any skeptic):
- The strict4 prompt is OUR pipeline's context-presentation layer, tuned on DEV; Mem0's
  frozen rows used the original neutral prompt (its stores would need re-querying to re-run,
  and its abstention was already 0.883 neutral). The core win does NOT depend on the prompt:
  neutral-prompt `dense` alone already beats Mem0 on answerable (DEV 0.519 vs 0.478) — the
  prompt work only repaired abstention.
- Judge = gpt-5 (single family); absolute numbers shift with a different judge, the paired
  difference is robust (both sides judged identically).
- `dense` requires an embeddings key at write+read (cached, deterministic, cents per
  conversation); the stdlib-only default path is unchanged and byte-identical.

Mem0 reference (frozen): DEV answerable 0.478, full-set 0.475, abstain 0.883.

## Iteration notes

### Iteration 0 — measurement infrastructure (2026-07-30)
- `scripts/mem0_locomo_e2e.py`: `--systems ours`, `--ours-features` A/B switchboard,
  answerer/judge disk caches keyed by (qid, model, effort, context-hash / normalized answer),
  campaign ledger with €30 hard cap; `scripts/mem0_e2e_report.py`: `--ours` vs
  `--mem0-baseline` pairing + `--convs` slicing.
- Caches seeded from the definitive run by regenerating baseline contexts locally
  (1986 answerer + 953 judge entries) → baseline DEV replay costs €0.000 and reproduces
  the run exactly (0.391/788, abstain 0.862). Unchanged contexts stay free in every
  future iteration; only changed contexts pay.

### Iteration 1 — adjacency windows, first measurement (2026-07-30)
- `window` (hit ± 1 neighbor turns, neighbors trimmed 280): same-slice paired delta on the
  601 answerable QA measured before the OpenAI account quota ran out: **+2.7 pts answerable**
  (0.384→0.411; +36 gained / −20 lost) — above the +1.5 promotion threshold. **BUT abstention
  fell 0.875→0.796 (−7.9 pts), violating the ≥0.85 Wächter gate** — richer neighbor context
  tempts the answerer into answering adversarial questions. NOT promoted as-is.
- Countermeasures implemented, ready to measure when credit returns:
  `window_prev` (preceding turn only, tighter trim — the taxonomy's coreference cases are
  mostly reply↔question pairs) and `strict` (abstention-hardened answerer prompt, cache-key
  versioned so baseline cache stays valid).
- Run crashed on OpenAI **account quota exhausted** (~75% through). Paid work is preserved in
  the caches (757 answerer + 195 judge new entries, ~€1.43 estimated, ledgered); resume will
  not re-pay. Library addition: `cognitive_memory/temporal.py` (deterministic relative-date
  annotation, 8 tests) — pending its own measurement as `temporal`.
- Gates re-verified locally during the outage: full suite OK, quality-gate PASS, reliability
  headline stable (governed 0.900, 0 violations, benign 1.0).

### Iterations 2–5 — single-feature attribution (2026-07-31)
- **The window family** (any direction) consistently trades abstention for answerable
  accuracy: both=+2.7/−7.9, prev=+0.4/−0.4, next=+1.5/−6.9 (all deltas in pts). Flip
  analysis: prev-window gains come from single-hop (+16/−8) but dilute temporal/multi-hop;
  the taxonomy's real adjacency case is the REPLY after a hit (next), which indeed lifts
  more than prev — but every added neighbor tempts the answerer into answering adversarial
  questions. Conclusion: windows need the `strict` abstention-hardened prompt to be viable.
- **`temporal` is aggregate-neutral** (±0.0 answerable, −1.7 abstain): open-domain category
  +2/−1, temporal +10/−12 — the date annotations shift BM25 token weights slightly and
  giveth/taketh away. Not promoted alone; may still help stacked on dense (different
  retrieval channel). Honest negative result.
- **`dense` (text-embedding-3-small + RRF over lexical top-50) is the breakthrough:
  answerable 0.391→0.519 (+12.8 pts), beating Mem0's DEV 0.478** — single-hop
  0.473→0.671, open-domain 0.164→0.262, multi-hop 0.212→0.258. Exactly the 30%
  paraphrase/attribute-colocation loss pattern the taxonomy predicted. Abstention broke the
  gate (0.797) like the windows did → `dense,strict` combo is the promotion candidate.

### Iterations 6–9 — calibrating abstention back without losing the dense win (2026-07-31)
Score-gated abstention was ruled out first with data: dense top-cosine distributions of
expected-abstain vs answerable questions barely separate (median 0.568 vs 0.609) — no usable
threshold. The lever is the answerer prompt, iterated with flip analysis:
- `strict` (v1, "only explicit statements"): abstain 0.888 ✓ but answerable crashes to 0.423 —
  it bans the inference that 12% of questions need.
- `strict2` (ban fabrication only, allow inference): answerable 0.543 (best), abstain 0.823.
  Flip analysis: the model now CORRECTS false premises ("No — Oscar is a guinea pig") instead
  of declining; scored wrong by the (Mem0-identical) abstention rule.
- `strict3` (+ never correct premises): abstain 0.841 = exact Mem0 parity, answerable 0.539.
  Remaining fails: mentioned-topic questions whose specific detail was never stated — the
  inference allowance overreaches into plausible guessing.
- `strict4` (+ infer only what is directly implied): **answerable 0.534 (+14.3 over baseline,
  +5.6 over Mem0's DEV 0.478), abstain 0.866 ✓ — above the 0.862 baseline. All DEV gates
  green; promotion candidate.** Full-set confirmation pending.

### Category-name correction (2026-07-31)
The harness's category display names for cats 2/3/4 were mislabeled. Verified against
Mem0's official run JSONs by question counts: cat1 n=282 = multi-hop, cat2 n=321 =
**temporal** (we had "single-hop"), cat3 n=96 = **open-domain** (we had "temporal"),
cat4 n=841 = **single-hop** (we had "open-domain"). All NUMBERS are unchanged — only
display names. Re-narrated final result: we win temporal 0.614 vs 0.442 (the
date-augmentation/temporal work paying off), single-hop 0.717 vs 0.592, multi-hop
0.411 vs 0.397; the statistical tie sits in open-domain (n=96).

### Published-numbers congruence check + bridge test (2026-07-31/08-01)
Before putting competitor numbers in the README we verified our Mem0 measurement against
everything published (research workflow, 3 agents, sources in ship_report):
- Landscape: Mem0 self 66.9 (2025, judge told to "be generous") → 82.7/91.6 (2026, top-50/200,
  gpt-5 CoT answerer, judge with partial credit + 14-day date tolerance); Zep self 75.1→94.7
  (after retracting an 84% figure); independent: ENGRAM paper Mem0=64.7 (k=20), LoCoMo-Refined
  strict human-validated judge Mem0=**48.9** — our strict-judge 0.509 matches the independent
  strict measurement almost exactly; our Opus-5 0.419 matches "Claude judges are strictest".
- **Bridge test:** scored BOTH systems' stored predictions with Mem0's OWN judge prompt
  (verbatim from mem0ai/memory-benchmarks, gpt-5): **Mem0 0.722** (inside its published band;
  residual gap to its 82.7 self-report = k=20 vs k=50 + answerer tier) and **ours 0.772
  (+5.0, p=4.4e-5) — we also win under their scoring.** Cost €5.16 (ledgered; OpenAI total
  ~€24.20/30). Category display names corrected against Mem0's official run JSONs
  (n=321 is temporal, n=841 single-hop, n=96 open-domain); numbers unchanged.
- README rebuilt: tier table, three-judge scoreboard, published-numbers context table,
  MVP history moved intact to docs/research_journal.md.

## Phase Z (2026-08-01): Zep as the third arm

- Config per Zep's own rebuttal checklist (user model, native created_at, parallel
  edge+node searches). Trial account. Ingestion: 272/272 sessions READ-BACK VERIFIED;
  graph processing confirmed at 5,788/5,882 episodes before eval. 1,986 QA, 0 errors.
- **Result: Provem 0.614 > Mem0 0.509 > Zep 0.449 (strict); ordering identical under
  claude-opus-5 (0.502/0.419/0.329) and Mem0's own judge (0.772/0.722/0.632);
  abstention 0.863/0.848/0.704. All pairwise McNemar significant (1e-35..7e-5).**
- Congruence: our Zep lands next to the independent ENGRAM-paper measurement (42.3),
  far from Zep's self-report (94.7) — same pattern as with Mem0's numbers.
- Two self-inflicted harness bugs cost ~3h and are the evening's lesson in verified
  writes: (1) tz-aware timestamps + appended "Z" made invalid created_at values →
  Zep's 400 "invalid json"; (2) the retry classifier matched the word "rate" which
  appears in EVERY Zep error via x-ratelimit headers → permanent 400s retried as 429s,
  first silently swallowed (chunk loop exhausted without raising), then honestly
  failing. Fixes: tz-conditional timestamps, retry only on literal status_code 429,
  never-swallow chunk failures, per-session read-back verification. A benchmarking
  project about governed, verified memory writes got burned twice in one evening by
  its own unverified API writes; the irony is documented on purpose.
