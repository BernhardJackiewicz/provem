# Provem

**Governed, GDPR-native memory for AI agents — stronger recall than Mem0, zero compliance violations, both proven.**

Two results, one system, every number reproducible from frozen artifacts at zero cost:

| Axis | Result |
|---|---|
| 🔒 **Governance** | Compliance violations **240 → 0**, memory-poisoning success **100% → 0%**, silent compounding errors **72.6% → 0.0%** (paired McNemar p ≈ 5×10⁻¹⁵⁰, deterministic, no API key) |
| 📦 **Memory** | **Beats Mem0 on full LoCoMo**: 0.614 vs 0.509 answerable accuracy (paired, +10.5 pts, p = 7×10⁻¹⁴) — confirmed under an independent Claude judge (+8.2, p = 5×10⁻¹⁰) **and under Mem0's own published judge prompt** (0.772 vs 0.722, p = 4×10⁻⁵) |

## What "governed" means — and why it matters professionally

The moment an agent *remembers*, it stops being a stateless tool and becomes a
**data-holding system** — with everything that legally and operationally implies.
Recall benchmarks measure whether memory helps the agent. Governance decides
whether you can **deploy** that memory: to real customers, under real
regulations, with real liability. Concretely, "governed" means each of these
enterprise requirements is enforced by the memory layer itself — not hoped for
in a prompt:

| Enterprise requirement | The incident without it | Provem mechanism |
|---|---|---|
| **Right to erasure** (GDPR Art. 17 & co.) | Agent quotes a customer's deleted data months later — now a reportable violation | Erasure enforced *at recall*, tenant-scoped, with signed erasure certificates |
| **Untrusted data sources** | A scraped page or tool output plants a false fact; it silently becomes "company knowledge" and re-fires forever (MINJA/AgentPoison-style poisoning) | Provenance + source-trust tagging, injection quarantine at write time, trust-weighted conflict resolution at read time |
| **Tenant isolation** | Customer A's salary data surfaces in customer B's session — a contract breach, not a bug | Hard scope isolation per tenant and entity, tested adversarially |
| **Auditability** | Regulator, customer, or court asks *"why did your AI say that?"* — and nobody can answer | SHA-256 hash-chained, tamper-evident audit log; every serve/refuse decision carries reasons and provenance |
| **Confident wrong answers** | Agent acts on a stale or forbidden fact; in multi-step workflows one bad memory corrupts every downstream step (measured: 2.12 steps each, ungoverned) | Calibrated abstention — a recoverable "I don't know" instead of a confident error; retention windows enforced |
| **Domain-specific rules** | Recruiting must honor do-not-contact and candidate confidentiality; pharma needs consent on health data; finance has retention duties — hardcoding this per app doesn't scale and can't be audited | Declarative compliance profiles per tenant (recruitment / pharma / finance / custom JSON-YAML), one server, many domains |

The reason this is a *layer* and not a feature: every team building agents
re-implements deletion, scoping, and audit ad hoc — in prompts, where nothing is
enforceable, or in app code, where nothing is auditable. Provem moves it into
the memory boundary, backend-agnostic, and **proves the effect end to end**: the
benchmark below shows the identical agent on the identical memory going from 240
compliance violations, 100% poisoning success and 72.6% silently corrupted steps
to **zero on all three** — while its recall stack independently beats the market
reference on the standard memory benchmark.

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
sh scripts/replay_report.sh     # every LoCoMo-vs-Mem0 number, €0, from frozen caches
```

## Choose your tier (honest numbers)

| Tier | LoCoMo answerable | Governance | Requirements |
|---|---|---|---|
| **stdlib default** | ~0.39–0.45 (below Mem0) | full | none — no key, no pip dependency, air-gap-safe |
| **dense (recommended)** | **0.614** (> Mem0 0.509) | full | embeddings API key (cents per conversation, disk-cached) |
| local embeddings | ~0.47–0.50 (projected) | full | planned: pip extra, no API key |

The stdlib tier is the zero-dependency governance layer and demo path — it does
not compete on recall, and we say so. The dense tier is the benchmarked
configuration.

## Evidence 1: the governance benchmark (deterministic, no API key)

Same recall backend, same 960 trajectories, same seeds — the only difference is
whether governance is on:

| Arm | Task success | Silent (compounding) errors | Poisoning success | Compliance violations | Benign accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| ungoverned memory | 0.375 | 72.6% of steps | 100% | 240 | 1.000 |
| **+ Provem governance** | **0.893** | **0.0%** | **0%** | **0** | **1.000** |

Paired exact McNemar p ≈ 5×10⁻¹⁵⁰; governance never loses a task the ungoverned
arm wins; benign accuracy stays 1.000 (calibrated, not blanket abstention). With
a stochastic agent, governance buys +43 to +52 points of end-to-end task success
at every skill level. Attack models are faithful analogs of MINJA
([arXiv:2503.03704](https://arxiv.org/abs/2503.03704)) and AgentPoison
([arXiv:2407.12784](https://arxiv.org/abs/2407.12784)). The agent is a
deterministic/noise-parametrized policy by design — it isolates the memory
layer's causal contribution; it is not an end-to-end LLM claim. Full method and
limits: [`docs/agentic_reliability_benchmark.md`](docs/agentic_reliability_benchmark.md),
[`docs/reliability_results.md`](docs/reliability_results.md).

## Evidence 2: memory quality vs Mem0 (full LoCoMo, paired, three judges)

Both systems ingested the same 10 LoCoMo conversations (5,882 turns) and answered
the same 1,986 questions with the **identical answerer model**; only the memory
differs. Mem0 ran on its own platform pipeline. A harness bug that silently
swallowed 12–23% of answers at the token cap was found and fixed **symmetrically
for both sides** before these numbers (full disclosure in
[`docs/lager_optimization_log.md`](docs/lager_optimization_log.md)).

| Scoring regime | **Provem (dense)** | Mem0 | Paired significance |
|---|---|---|---|
| Strict binary judge (gpt-5) | **0.614** | 0.509 | p = 7.2×10⁻¹⁴ |
| Independent cross-vendor judge (claude-opus-5) | **0.502** | 0.419 | p = 5.1×10⁻¹⁰ |
| **Mem0's own published judge prompt** (partial credit, 14-day date tolerance) | **0.772** | 0.722 | p = 4.4×10⁻⁵ |
| Abstention on 446 adversarial questions | **0.863** | 0.848 | parity (p = 0.49) |

Per category (strict judge): temporal **0.614 vs 0.442**, single-hop
**0.717 vs 0.592**, multi-hop **0.411 vs 0.397**, open-domain 0.312 vs 0.333
(statistical tie, n=96). Holdout conversations never used for tuning show the
*larger* win (+11.4 pts, p = 1.7×10⁻⁸).

### The LoCoMo landscape (read before quoting any of it)

LoCoMo scores are **not comparable across papers** — they move ±30 points with
the judge prompt, retrieval depth, and answerer. Merging them into one
leaderboard is exactly the methodological sin the vendors accuse each other of,
so we present three separately-valid rankings instead.

**Ranking 1 — measured in this repo, same harness (the only ranking we claim):**

| Rank | System | Strict judge | Mem0's own judge | Abstention |
|---|---|---|---|---|
| 1 | **Provem (dense tier)** | **0.614** | **0.772** | 0.863 |
| 2 | Mem0 platform | 0.509 | 0.722 | 0.848 |

Identical questions, answerer, and judge for both rows; paired McNemar
p = 7×10⁻¹⁴ (strict) / 4×10⁻⁵ (Mem0's judge). We rank only what we measured.

**Ranking 2 — independent third-party evaluations (quoted verbatim, their setups):**

| ENGRAM paper (arXiv 2511.12960)¹, k=20, gpt-4o-mini | J | | LoCoMo-Refined (strict judge, 86% human agreement) | score |
|---|---|---|---|---|
| ENGRAM (academic system¹) | 77.6 | | MemoraX AI | 82.7 |
| MemOS | 73.0 | | MemOS | 63.6 |
| **Mem0** | **64.7** | | MemPalace | 58.7 |
| LangMem | 55.3 | | EverMemOS | 58.3 |
| OpenAI Memory | 52.8 | | **Mem0** | **48.9** |
| Zep | 42.3 | | | |

¹ Unrelated academic system (arXiv 2511.12960), no relation to this project.

**The anchor that connects the tables:** our strict-judge Mem0 measurement
(0.509) matches LoCoMo-Refined's strict Mem0 (48.9) almost exactly, and under
Mem0's own judge our Mem0 lands at 0.722 — inside its published band. Our
harness reproduces what independent evaluations find; we did not measure Zep,
MemOS, or the others head-to-head, so we make no claims against them.

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
- **LLM judges only** (two vendors, κ = 0.70–0.83 agreement); no human eval yet.
- Answer prompts were tuned on a dev split — but the win survives with a fully
  neutral prompt (+8.7 pts) and on held-out conversations (+11.4 pts).
- Mem0 ran with platform defaults; a Mem0 expert might configure it better. Its
  stores kept consolidating between runs (drift favors Mem0).
- The stdlib tier trails Mem0 on recall (~0.39–0.45); keyless parity is not
  realistic and we don't claim it.
- The governance benchmark uses a scripted agent by design (causal isolation).
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

---
*Formerly developed under the working name "Engram"; renamed to avoid collision with unrelated 2026 products of that name.*
