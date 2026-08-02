# Provem

**Governed, GDPR-native memory for AI agents — stronger measured recall than Mem0 and Zep on LoCoMo, and zero compliance violations across the 960-trajectory governance benchmark.**

Two results, one system, every number reproducible from frozen artifacts at zero cost:

| Axis | Result |
|---|---|
| **Governance** | Compliance violations **240 → 0**, memory-poisoning success **100% → 0%**, silent compounding errors **72.6% → 0.0%** (paired: governance flips 497 of 960 trajectories, loses 0; deterministic, no API key) |
| **Memory** | **Beats Mem0 and Zep on full LoCoMo**: 0.614 vs 0.565 vs 0.449 answerable accuracy (paired; Provem>Mem0 p = 2.5×10⁻⁴, Provem>Zep p = 1×10⁻³⁴) — the ordering holds under an independent Claude judge **and under Mem0's own published judge prompt** (0.772 / 0.716 / 0.649). Both baselines re-measured after fixing input bugs that had understated them |

## The problem

Take a recruiting agent. It picks up information from everywhere: email, the
CRM, Slack, web pages, PDFs, meetings, the user chat. All of it lands in a
memory, and most agent memories work the same way: store → embed → retrieve.
This works surprisingly well.

Then the memory grows, and the hard question changes. It is no longer
*"can I find it?"* — it becomes ***"am I allowed to use it?"***

A candidate writes: *"Please delete my salary expectation."* Three months later
the agent uses it anyway. The retrieval was perfect. **The memory was legally
wrong.**

The same shift shows up everywhere once agents hold data about real people and
real companies:

- A scraped page plants a wrong fact — it quietly becomes "knowledge" and
  re-fires in every future answer (this is how MINJA/AgentPoison-style attacks
  work).
- Customer A's data surfaces in customer B's session.
- Someone asks *"why did the agent say that?"* — and there is no answer.
- The agent states a stale fact confidently instead of saying "I don't know",
  and in a multi-step workflow that error repeats (measured below: one bad
  memory re-fires in 2.12 later steps of the scripted workflow on average).

Better retrieval fixes none of this. These are governance problems, and today
they are mostly "solved" in prompts (unenforceable) or in per-app code
(unauditable).

## What Provem is

Provem is an open-source research project: a governance layer that sits between
the agent and whatever actually stores the memories.

```text
   Agent  (any model: GPT, Claude, Llama, Gemini, ...)
     |
     v
   Provem   decides:  may this be stored?   may it be read?
     |                whose data is it?     was it deleted?
     |                do I trust the source?
     |                should I rather say "I don't know"?
     v
   any backend:  SQLite · BM25 · Mem0 · Zep/Graphiti · your own DB
```

Two design decisions make the pieces swappable:

- **Backend-agnostic.** The storage engine is a plug-in behind a small
  `MemoryBackend` protocol. Erasure, scoping, trust, and audit live *above* the
  store and do not change when you swap it.
- **Model-agnostic.** The rules have nothing to do with which LLM you run.
  Support on GPT, legal on Claude, internal tools on Llama? The erasure duty is
  the same, tenant isolation is the same, the audit trail is the same. One
  governance layer — instead of re-implementing compliance once per model and
  once per memory vendor.

Concretely, the layer enforces:

| Requirement | Without it | Provem mechanism |
|---|---|---|
| Right to erasure (GDPR Art. 17 & co.) | Agent quotes deleted data months later | Erasure enforced *at recall*, tenant-scoped, with erasure certificates |
| Untrusted sources | A planted fact becomes permanent "knowledge" | Provenance + trust tagging, injection quarantine at write, trust-weighted conflict resolution at read |
| Tenant isolation | Customer A's data in customer B's session | Hard scope isolation per tenant and entity, tested adversarially |
| Auditability | *"Why did the agent say that?"* has no answer | SHA-256 hash-chained audit log; every serve/refuse decision carries reasons and provenance |
| Calibrated uncertainty | Confident stale answers that compound | Abstention as a first-class outcome; retention windows enforced |
| Domain rules | Recruiting ≠ pharma ≠ finance, hardcoded per app | Declarative per-tenant compliance profiles (JSON/YAML) |

This is a research project first: every claim on this page has a reproducible
benchmark behind it, negative results are documented alongside the wins, and
the whole evidence chain replays from frozen artifacts at zero cost.

## Quick start

```bash
pip install -e .            # dependency-free core, Python 3.9+
```

Wrap a memory in four lines:

```python
from cognitive_memory.reliability import GovernedMemory, NaiveBackend, Scope

mem = GovernedMemory(NaiveBackend(), policy="recruitment")   # or "pharma", "finance", custom JSON/YAML
mem.remember("cand_1 salary_target 120k", subject="cand_1", relation="salary_target",
             object="120k", tenant="acme", entity="cand_1", source="recruiter", trust=0.9)
mem.remember("cand_1 salary_target 80k",  subject="cand_1", relation="salary_target",
             object="80k",  tenant="acme", entity="cand_1", source="scraper_tool", trust=0.4)  # poisoning attempt
mem.forget("migraine", Scope("acme", "cand_1"))              # GDPR erasure — enforced at recall, certificate issued

mem.recall_value("cand_1 salary_target", tenant="acme", entity="cand_1").answer
# -> "120k"   (trusted source wins; never the poisoned, erased, or cross-tenant value)
```

Run it as a configurable MCP server (one server, many tenants, per-tenant
compliance profiles, hash-chained audit):

```bash
PYTHONPATH=src python3 -m cognitive_memory mcp-serve --config examples/mcp/server_config.json
```

Reproduce the headline numbers:

```bash
PYTHONPATH=src python3 -m cognitive_memory reliability --seeds 1,2,3,4,5,6,7,8,9,10 --scenarios 96
sh scripts/fetch_locomo.sh      # one-time: fetch LoCoMo (CC BY-NC 4.0, not redistributed here), sha256-verified
sh scripts/replay_report.sh     # every three-system number, €0, from frozen caches
```

## Choose your tier (honest numbers)

| Tier | LoCoMo answerable | Governance | Requirements |
|---|---|---|---|
| stdlib retrieval + LLM answerer | 0.388 (below Mem0) | full | LLM API key for answering; no pip dependency |
| fully keyless (extractive answers) | 0.21 — below the 0.24 no-memory baseline | full | none — no key, no pip dependency, air-gap-safe |
| **dense (recommended)** | **0.614** (> Mem0 0.565 > Zep 0.449) | full | embeddings API key (cents per conversation, disk-cached) |
| local embeddings | ~0.47–0.50 (projected, unbuilt) | full | planned: pip extra, no API key |

The keyless tier is the zero-dependency governance layer and demo path — it
does not compete on recall (its extractive answering scores below a no-memory
abstain baseline on LoCoMo, measured 0.207–0.212), and we say so. The dense
tier is the benchmarked configuration.

## Evidence 1: the governance benchmark (deterministic, no API key)

Same recall backend, same 960 trajectories, same seeds — the only difference is
whether governance is on:

| Arm | Task success | Silent (compounding) errors | Poisoning success | Compliance violations | Benign accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| ungoverned memory | 0.375 | 72.6% of steps | 100% | 240 | 1.000 |
| **+ Provem governance** | **0.893** | **0.0%** | **0%** | **0** | **1.000** |

Paired per trajectory: governance flips 497 of 960 trajectories from fail to
pass and never loses one the ungoverned arm wins (497/0 discordant). We report
counts, not p-values, here on purpose: the benchmark is a deterministic,
author-designed simulation, so a McNemar p-value would only restate the chosen
scenario count. Benign accuracy stays 1.000 — by construction: benign probes
are designed so no governance mechanism can fire, which verifies governance
does not interfere, not that it is calibrated on hard cases. With a stochastic
agent, governance buys +43 to +52 points of end-to-end task success at every
skill level. Attack models are simplified analogs of the MINJA
([arXiv:2503.03704](https://arxiv.org/abs/2503.03704)) and AgentPoison
([arXiv:2407.12784](https://arxiv.org/abs/2407.12784)) mechanisms — see the
attack-family results and their limits, including the same-channel poisoning
boundary governance cannot catch. The agent is a deterministic/
noise-parametrized policy by design — it isolates the memory layer's causal
contribution; it is not an end-to-end LLM claim. Full method and limits:
[`docs/agentic_reliability_benchmark.md`](docs/agentic_reliability_benchmark.md),
[`docs/reliability_results.md`](docs/reliability_results.md).

## Evidence 2: memory quality vs Mem0 and Zep (full LoCoMo, paired, three judges)

All three systems ingested the same 10 LoCoMo conversations (5,882 turns) and
answered the same 1,986 questions with the **identical answerer model**; only
the memory differs. Mem0 ran on its own platform pipeline; Zep ran on Zep Cloud,
configured per **Zep's own published evaluation checklist** (proper user model,
native `created_at` timestamps, parallel edge+node graph searches, chronological
ingestion), with ingestion read-back verified and full graph completion
hard-gated before evaluation. Both baseline arms were **re-measured** after an
audit found two input bugs that understated them (Mem0 got a doubled date
prefix; Zep ingested sessions in lexical order) — full v1→v2 disclosure in
[`docs/measurement_changelog.md`](docs/measurement_changelog.md).

| Scoring regime | **Provem (dense)** | Mem0 | Zep |
|---|---|---|---|
| Strict binary judge (gpt-5) | **0.614** | 0.565 | 0.449 |
| Independent cross-vendor judge (claude-opus-5) | **0.502** | 0.368 | 0.304 |
| **Mem0's own published judge prompt** (partial credit, 14-day date tolerance) | **0.772** | 0.716 | 0.649 |
| Abstention on 446 adversarial questions | **0.863** | 0.830 | 0.722 |

**The ordering is invariant under all three judges.** All pairwise differences
in **answerable accuracy** are significant (paired McNemar: Provem>Mem0
p = 2.5×10⁻⁴, Provem>Zep p = 1.1×10⁻³⁴, Mem0>Zep p = 3.1×10⁻¹⁵). Fixing Mem0's
input bug raised it from 0.509 to 0.565, so the Provem→Mem0 strict lead is
**+4.9 pts, not the +10.5 pts v1 reported** — still significant, but roughly
half. (The judges disagree on the gap's size: opus scores the corrected Mem0 at
0.368, a wider lead, rejecting more of the extra borderline answers Mem0 now
attempts; strict-vs-opus κ for Mem0 is 0.54.) The abstention edge over Mem0
(0.863 vs 0.830) is a statistical tie (p = 0.12), and Provem's 0.863 comes from
its dev-tuned prompt chain — under the shared neutral prompt Provem's abstention
is 0.693, behind Zep 0.722 and Mem0 0.830 (answerable accuracy still wins under
that neutral prompt: 0.596 vs 0.565). Per category (strict judge): single-hop
**0.717 / 0.672 / 0.566**, temporal **0.614 / 0.523 / 0.290**, multi-hop
**0.411 / 0.394 / 0.330** (narrow), open-domain 0.312 / 0.271 / 0.312 (Provem/Zep
tie, n=96). Holdout conversations (final config chosen on the dev split
only) confirm the ordering (0.617 / 0.582 / 0.469). Full methodology, configs,
and limitations:
[`docs/three_system_benchmark.md`](docs/three_system_benchmark.md).

### The LoCoMo landscape (read before quoting any of it)

LoCoMo scores are **not comparable across papers** — they move ±30 points with
the judge prompt, retrieval depth, and answerer. Merging them into one
leaderboard is exactly the methodological sin the vendors accuse each other of,
so we present three separately-valid rankings instead.

**Ranking 1 — measured in this repo, same harness (the only ranking we claim):**

| Rank | System | Strict judge | Claude judge | Mem0's own judge | Abstention |
|---|---|---|---|---|---|
| 1 | **Provem (dense tier)** | **0.614** | **0.502** | **0.772** | **0.863** |
| 2 | Mem0 platform | 0.565 | 0.368 | 0.716 | 0.830 |
| 3 | Zep platform | 0.449 | 0.304 | 0.649 | 0.722 |

Identical questions, answerer, and judges for every row. All pairwise
differences in answerable accuracy are significant (paired McNemar:
Provem>Mem0 p = 2.5×10⁻⁴, Provem>Zep p = 1×10⁻³⁴, Mem0>Zep p = 3×10⁻¹⁵) and the
ordering is identical under all three judges; the abstention column's
Provem-vs-Mem0 gap is a statistical tie and prompt-confounded (see above). Zep
was configured following its own published evaluation checklist (proper user
model, native created_at timestamps, parallel edge+node graph searches) to
pre-empt the misconfiguration critique it raised against Mem0's paper; config
details in the ship report. We rank only what we measured.

**Ranking 2 — independent third-party evaluations (quoted verbatim, their
setups; two separate leaderboards, not comparable to each other):**

ENGRAM paper (arXiv 2511.12960)¹, k=20, gpt-4o-mini judge:

| System | Score |
|---|---|
| ENGRAM (academic system¹) | 77.6 |
| MemOS | 73.0 |
| **Mem0** | **64.7** |
| LangMem | 55.3 |
| OpenAI Memory | 52.8 |
| Zep | 42.3 |

LoCoMo-Refined (strict judge, 86% human agreement):

| System | Score |
|---|---|
| MemoraX AI | 82.7 |
| MemOS | 63.6 |
| MemPalace | 58.7 |
| EverMemOS | 58.3 |
| **Mem0** | **48.9** |

¹ Unrelated academic system (arXiv 2511.12960), no relation to this project.

**The anchor that connects the tables:** our strict-judge Mem0 measurement
(0.565, corrected) sits just above LoCoMo-Refined's strict Mem0 (48.9),
consistent with our fully-settled stores and single-date input; our
strict-judge Zep (0.449) lands next to the ENGRAM paper's independent Zep
(42.3) — and far from Zep's self-reported 94.7. Under Mem0's own judge our
Mem0 lands at 0.716, inside its published band. Our harness reproduces what
independent evaluations find. MemOS and the remaining systems were not
measured head-to-head, so we make no claims against them.

**Ranking 3 — vendor self-reports (marketing conditions, listed for completeness):**
Zep 94.7 (gpt-5.4 CoT reader/judge; after retracting an earlier 84% figure) ·
Mem0 92.5 / 91.6 (top-200 memories, gpt-5 CoT answerer, judge with partial
credit + 14-day date tolerance) · MemMachine 91.7 · Letta 74.0. Each was
produced by the vendor under conditions of its choosing; none is comparable to
any other number on this page.

Sources and the full dispute history (including who retracted what):
[`docs/ship_report.md`](docs/ship_report.md).

## What the governance layer does

- **Write-side:** prompt-injection quarantine, sensitive-without-consent hold,
  provenance + source-trust tagging, natural-language erasure/do-not-use intents.
- **Read-side:** erasure & do-not-use enforcement, tenant/entity scope isolation,
  source-conflict resolution by provenance trust, retention enforcement, and
  **calibrated abstention** — a recoverable "I don't know" instead of a confident
  wrong answer that compounds.
- **Accountability:** SHA-256 hash-chained tamper-evident audit log, GDPR erasure
  certificates, per-tenant compliance profiles (recruitment / pharma / finance /
  custom JSON-YAML), all decisions traceable.

## Honest limitations

- **One recall benchmark** (LoCoMo). LongMemEval port is designed, not run.
- **LLM judges only** (two vendors, κ 0.54 on Mem0 to 0.70 on Provem on the final
  artifacts); no human eval yet.
- Answer prompts were tuned on a dev split — the answerable-accuracy win
  survives with a fully neutral prompt (+3.1 pts vs the corrected Mem0 0.565)
  and on held-out conversations (+3.5 pts, though the holdout gap alone is not
  significant, p=0.074), but the abstention headline does not: under the neutral prompt
  Provem's abstention is 0.693, last of the three systems.
- Mem0 ran with platform defaults; a Mem0 expert might configure it better. Its
  stores kept consolidating between runs (drift favors Mem0).
- Keyless parity is not realistic and we don't claim it: with stdlib retrieval
  and an LLM answerer we measure 0.388; fully keyless (extractive answering)
  measures 0.207–0.212, below the 0.237 no-memory abstain baseline.
- The governance benchmark uses a scripted agent by design (causal isolation).
- **Governance defends untrusted-channel attacks, not same-channel ones.** A
  poison delivered through the same fully-trusted channel as the user (equal
  trust, later write) is served by both arms — provenance has no signal there;
  it needs write-side detection/review. Measured and reported in the
  attack-family benchmark (`--attack-families`), not hidden.
- No external security audit; not "production-certified"; self-hosted only.

The complete disclosure list, where Mem0 remains genuinely better (curated
human-readable memories, managed hosting), and the ship recommendation:
[`docs/ship_report.md`](docs/ship_report.md).

## Architecture

```text
Agent / LLM caller
      |
      v
GovernedMemory (backend-agnostic wrapper; MemoryBackend protocol)
      +-- write gate: injection quarantine, trust tagging, consent, retention
      +-- storage:    verbatim episodes + temporal facts (Naive / BM25 / SQLite / Mem0-adapter)
      +-- retrieval:  lexical BM25 + optional dense embeddings + RRF fusion
      +-- read gate:  erasure / scope / trust conflicts / relevance floor / abstention
      +-- audit:      SHA-256 hash-chained log + erasure certificates
      |
      v
MCP server (JSON-RPC/stdio, per-tenant profiles)   or   direct library embedding
```

## Documentation

| Doc | What it contains |
|---|---|
| [`docs/three_system_benchmark.md`](docs/three_system_benchmark.md) | The headline benchmark: Provem vs Mem0 vs Zep, three judges, paired stats |
| [`docs/ship_report.md`](docs/ship_report.md) | Final scoreboard, full limitations, ship recommendation |
| [`docs/lager_optimization_log.md`](docs/lager_optimization_log.md) | Every optimization iteration incl. failures and the bug post-mortem |
| [`docs/reliability_results.md`](docs/reliability_results.md) | Governance benchmark, full statistics |
| [`docs/mcp_server.md`](docs/mcp_server.md) | MCP product guide, profiles, config |
| [`docs/trust_model.md`](docs/trust_model.md) | Security boundaries; what belongs in a gateway |
| [`docs/claim_register.md`](docs/claim_register.md) | Every claim with evidence level and risk |
| [`docs/research_journal.md`](docs/research_journal.md) | Complete MVP history, every synthetic suite, every negative result |
| [`docs/runs/manifest.json`](docs/runs/manifest.json) + `scripts/replay_report.sh` | Bit-exact €0 reproduction of all benchmark numbers |

## Status

Research-grade core with enterprise-ready foundations: 490 tests, deterministic
quality gates, tamper-evident audit, tenant isolation, configurable compliance
profiles. **Not** externally security-audited, no managed hosting, no SLA — the
enterprise wrapper (gateway auth/SSO, hosting, certifications) is deliberately
out of scope for the core and documented in the trust model. Roadmap: LongMemEval
port, local-embeddings tier, cross-session fact rollups, judge-diverse human eval.

