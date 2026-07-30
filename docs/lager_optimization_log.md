# Lager optimization campaign log — beating Mem0 on LoCoMo E2E

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
