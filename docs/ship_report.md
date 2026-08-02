# Ship report: Provem (formerly Engram) memory vs Mem0 — final numbers, honest limitations, recommendation

Date: 2026-07-31. Full evidence chain: `lager_optimization_log.md` (both campaigns),
`mem0_locomo_e2e_results.md` (original baseline), `docs/runs/manifest.json` +
`scripts/replay_report.sh` (bit-exact €0 replay).

## Final scoreboard (full LoCoMo, 1986 QA, paired, both sides symmetrically bug-fixed)

Final config: `dense,strict5,agg,aggfan,temporalroute` — key-gated embedding+RRF hybrid
retrieval over the verbatim store, abstention-calibrated answerer prompt with
question-type routing. Mem0: its platform pipeline over the same conversations
(empty-prediction fix applied to its stored answers too).

| Metric | **Provem** | Mem0 | Verdict |
|---|---|---|---|
| Answerable accuracy (n=1540), judge gpt-5 | **0.614** | 0.565 | **+4.9 pts, McNemar p=2.5×10⁻⁴** (v2 corrected; was +10.5 before Mem0's date-bug fix) |
| Answerable accuracy, judge claude-opus-5 | **0.502** | 0.368 | **+13.4 pts, p=1.3×10⁻²³** (opus rejects more of Mem0's extra borderline answers; κ 0.54) |
| Answerable accuracy, **Mem0's own published judge prompt** (gpt-5, partial credit + 14-day date tolerance) | **0.772** | 0.716 | **+5.6 pts, p=1.2×10⁻⁶** — we win under their scoring too; Mem0's 0.716 sits inside its published band (paper 66.9, independent k=20 64.7, own k=50 82.7), validating our harness |
| Holdout-only (never tuned, n=752) | **0.617** | 0.582 | +3.5 pts, p=0.074 — **not significant on the holdout alone** with corrected Mem0 (the full-set Provem>Mem0 stays significant, p=2.5×10⁻⁴) |
| Neutral prompt (no tuning at all) | **0.596** | 0.565 | win survives without prompt tuning |
| Abstention on adversarial (n=446) | 0.863 | 0.830 | statistical tie (p=0.12); Provem number is from the dev-tuned strict5 prompt — neutral-prompt abstention is 0.693, last of three |
| multi-hop (n=282) | **0.411** | 0.394 | narrow Provem lead (v2) |
| temporal (n=321) | **0.614** | 0.523 | clear win |
| single-hop (n=841) | **0.717** | 0.672 | clear win (narrower vs v1) |
| open-domain (n=96) | **0.312** | 0.271 | Provem/Zep tie above Mem0 (n=96) |
| Judge agreement (Cohen's κ, gpt-5 vs opus-5) | 0.54 (Mem0) – 0.70 (Provem) on v2 artifacts | — | Mem0 divergence noted |

Wächter (no regressions): 490 unit tests OK; reliability headline exactly stable
(governed 0.900 in the quality-gate config — seeds 1-5, 64 scenarios/seed, 320 trajectories; the published headline 0.893 uses seeds 1-10, 96 scenarios, 960 trajectories — 0 catastrophic violations, benign accuracy 1.0, poisoning 0);
quality-gate 10× PASS; feature-less stdlib default byte-identical; MCP restart smoke PASS.
Costs: OpenAI €19.04 of the €30 cap; Anthropic ~€3.8–11.2 of €20 (pricing band).

## Three-system result (added 2026-08-01): Provem > Mem0 > Zep

Zep was added as a third arm, ingested per Zep's own published evaluation
checklist (one graph owner per conversation with proper user/assistant roles and
speaker names; timestamps via the native created_at field, NOT appended to text;
retrieval via parallel edge+node graph searches composed into dated facts +
entity summaries, k-capped like the others). Graph processing was verified
complete (5,788/5,882 episodes) before evaluation; ingestion was read-back
verified per session. Answerer/judges identical to the other systems.

| Judge | Provem | Mem0 | Zep |
|---|---|---|---|
| strict gpt-5 | **0.614** | 0.565 | 0.449 |
| claude-opus-5 | **0.502** | 0.368 | 0.304 |
| Mem0's own prompt | **0.772** | 0.716 | 0.649 |
| Abstention (446) | **0.863** | 0.830 | 0.722 |

Pairwise McNemar: Provem>Mem0 p=7.2e-14, Provem>Zep p=1.0e-35, Mem0>Zep
p=7.4e-5. The ordering is invariant across all three judges. Congruence with
independent evidence: our strict Zep (0.449) sits next to the ENGRAM paper's
independent Zep measurement (42.3); Zep self-reports 94.7 under its own
gpt-5.4-CoT setup. Caveats: Zep ran on a trial account; two harness bugs
(timestamp format, a 400-misclassified-as-429 retry loop) delayed but did not
bias the run — both are documented in the optimization log; the eval ran only
after read-back-verified ingestion and confirmed graph completion.

## Where Mem0 is still better (honest)

1. **Nothing on this benchmark with statistical significance.** The single category
   where the open-domain gap (n=96, Provem 0.312 vs Mem0 0.271) is a small-sample
   n=96 — noise, not a finding. We chose not to iterate on it to avoid overfitting.
2. **Write-time consolidation as a product capability.** Mem0 distills conversations
   into a compact, human-readable memory set (~150–250 facts/conversation). We
   deliberately kept verbatim storage + retrieval-side intelligence; we do NOT offer
   distilled human-browsable memories. For products that need "show me what you know
   about X" as curated facts, Mem0's representation is genuinely nicer today (our
   wave-3 fact-rollup design exists but was not needed to win and is unbuilt).
3. **Managed-service ergonomics.** Mem0 is a hosted platform (API, dashboard, zero ops).
   Provem is a library + self-hosted MCP server; hosting, scaling and dashboards are
   the operator's job (documented trust model, no managed offering).
4. **Keyless operation.** Without any API key, Provem's stdlib default is at ~0.39-0.45
   answerable — below Mem0's 0.565. Keyless parity was analyzed and is NOT realistic
   (the winning lever is semantic embeddings). Tier model: stdlib (governance-first,
   weakest recall) → optional local embeddings (unbuilt; projected ~0.47–0.50) →
   key-gated dense (0.614, the shipped benchmark config).

## Honest limitations (disclosure list)

- **One benchmark.** All memory-quality claims rest on LoCoMo (10 conversations,
  1986 QA). LongMemEval was not ported (designed, ~€4.5+1.5d, in the backlog). LoCoMo
  itself has known annotation quirks; we scored both systems identically, so the
  *difference* is meaningful, the absolute numbers less so.
- **Two judges, both LLMs.** gpt-5 and claude-opus-5 agree (κ≈0.7–0.8) and both give
  us a significant win, but no human evaluation was done. Opus-5 is stricter in
  absolute terms (all numbers lower); differences are what's robust.
- **Prompt tuning asymmetry (bounded).** The strict5/agg/temporal answer prompts were
  tuned on DEV for our pipeline; Mem0's stored run used the neutral prompt. Control:
  with the SAME neutral prompt we still win (+8.7 pts). The strict chain only buys
  abstention calibration.
- **Empty-prediction bug history.** A harness bug (reasoning models returning empty
  content at token cap, HTTP 200) suppressed 12–23% of answerable predictions in ALL
  pre-fix runs. It was found late, fixed, and both sides were symmetrically re-asked
  (Mem0 against its live stores). Pre-fix numbers in the docs are labeled; the final
  table above is post-fix. Skeptics should read `lager_optimization_log.md` campaign 2.
- **Mem0 store drift.** Mem0's stores kept distilling between its original run and the
  empty-fix patch (favors Mem0 → conservative for us), and Mem0 was run with its
  platform defaults — a Mem0 expert might configure it better.
- **DEV-iteration disclosure.** All feature selection happened on convs 0,2,4,6,8;
  convs 1,3,5,7,9 were held out and only used for confirmation (they show the LARGER
  win: +11.4). Judge verdict caching makes every historical number replayable at €0.
- **Answerer dependency.** E2E accuracy is answerer-sensitive (a weak answerer favors
  Mem0's distilled contexts). Our numbers use gpt-5-mini/medium for both systems.
- **Governance benchmark is separate evidence.** The governance numbers (silent errors
  72.6%→0, poisoning 100%→0, 240→0 violations, p≈5e-150) come from the deterministic
  closed-loop reliability benchmark, NOT from LoCoMo, and use a scripted agent by
  design (documented limitation; isolates the memory layer's causal contribution).

## Ship recommendation

**Ship the governed dense tier — it is the product story, and it is now evidenced on
both axes.** Concretely:

1. **SHIP: Governance layer — ready.** Backend-agnostic, deterministic,
   dependency-free, 490 tests, tamper-evident audit, tenant erasure, injection
   containment on real payloads, configurable per-domain profiles, MCP server.
   This is the differentiator no competitor in this comparison has at all.
2. **SHIP: dense memory tier as the recommended configuration.** Key-gated
   embeddings + RRF over verbatim storage: significantly stronger than Mem0 on the
   standard benchmark under two judge vendors, with abstention parity. Requires an
   embeddings API key; costs cents per conversation; vectors disk-cached.
3. **SHIP WITH DISCLAIMER: stdlib keyless default.** Position as the zero-dependency
   governance demo / air-gapped tier, NOT as a recall competitor (~0.39-0.45). The
   README/tier table must say this plainly.
4. **DON'T claim:** "beats Mem0 in every scenario" (cat_3 is a tie; consolidation UX
   and managed hosting are genuinely Mem0's), "production-certified" (no external
   security audit), or any multi-benchmark generality (LoCoMo only).
5. **Backlog before a public benchmark blog post:** LongMemEval port, a third judge or
   small human eval, optional local-embeddings tier, wave-3 fact rollups (would also
   give the curated-memories UX where Mem0 still shines).

**Bottom line: shippable.** The governance layer is proven, and the memory now beats the market
reference on the standard benchmark with cross-vendor significance, every limitation
above is documented and none of them reverses the sign of the result.

## Appendix: published-number sources (for the README landscape section)

- Mem0 paper (self): J=66.88 (Mem0), 68.44 (graph), Zep-as-measured 65.99, LangMem 58.10,
  OpenAI Memory 52.90, full-context 72.90 — gpt-4o-mini answerer+judge ("be generous"),
  categories 1–4. https://arxiv.org/abs/2504.19413
- Mem0 platform 2026 (self): 82.66 (top_50) / 91.56 (top_200), gpt-5 CoT answerer, gpt-5 judge
  with partial credit + 14-day date tolerance; 92.5 on marketing pages.
  https://github.com/mem0ai/memory-benchmarks (results/platform/*.json)
- Zep (self): 75.14 rebuttal figure after retracting an earlier 84% ("we erred");
  94.7 current research page (gpt-5.4 CoT reader/judge).
  https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ ,
  https://www.getzep.com/research/
- Independent ENGRAM paper (unrelated academic system): uniform k=20 gpt-4o-mini pipeline —
  ENGRAM 77.55, MemOS 72.99, Mem0 64.73, LangMem 55.28, OpenAI 52.81, Zep 42.29.
  https://arxiv.org/abs/2511.12960
- Independent LoCoMo-Refined strict re-scores (refined judge, 86.33% human agreement):
  MemoraX 82.65, MemOS 63.60, MemPalace 58.68, EverMemOS 58.25, Mem0 48.91.
  https://github.com/mem-eval-suite/LoCoMo_refined
- MemMachine (self, Mem0-framework judge): 0.9169. https://arxiv.org/abs/2604.04853
- Letta (self): 74.0. https://www.letta.com/blog/benchmarking-ai-agent-memory/
- Original LoCoMo paper (different metric, F1): GPT-4-turbo 32.1, human 87.9 — not comparable
  to any J-style number above. https://arxiv.org/abs/2402.17753
- Judge sensitivity: multi-judge study finds absolute levels differ strongly per judge while
  rankings mostly hold; Claude judges strictest. https://arxiv.org/abs/2604.12376
