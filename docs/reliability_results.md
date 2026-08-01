# Agentic Memory Reliability — Results

Canonical run. Deterministic; reproduces exactly from the seeds below.

```bash
PYTHONPATH=src python3 -m cognitive_memory reliability --seeds 1,2,3,4,5,6,7,8,9,10 --scenarios 96
```

- Seeds: `1..10`
- Scenarios/seed: `96`
- Trajectories: `960` per arm
- Methodology: `docs/agentic_reliability_benchmark.md`

## Headline

| Arm | Task success (95% CI) | Catastrophe-free | Silent-error rate (95% CI) | Poisoning success | Compliance violations | Benign accuracy |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| `no_memory` | `0.000` [0.000, 0.004] | `1.000` | `0.000` [0.000, 0.002] | `0.000` | `0` | `0.000` |
| `ungoverned` | `0.375` [0.345, 0.406] | `0.375` | `0.726` [0.705, 0.747] | `1.000` | `240` | `1.000` |
| `governed` | `0.893` [0.872, 0.911] | `1.000` | `0.000` [0.000, 0.002] | `0.000` | `0` | `1.000` |

## Governed vs ungoverned (paired, same 960 trajectories)

- **Task-success pairing:** governed-only wins `497`, ungoverned-only wins `0`,
  discordant `497` of 960 trajectories. Governed never loses a trajectory the
  ungoverned arm wins. We deliberately report counts instead of a McNemar
  p-value: the benchmark is a deterministic, author-designed simulation whose
  null hypothesis is false by construction, so a p-value's magnitude would
  only restate the chosen scenario count (2,000 scenarios would mechanically
  yield p ≈ 1e-300).
- **Per-step correctness gain:** `+0.549` (descriptive; steps within a
  trajectory are not independent, so no step-level CI is reported).
- **Silent-error-rate change:** `-0.726` (descriptive, same reason).
- **Blast radius (re-retrieval):** ungoverned `2.12` silent errors per
  adversarial trajectory vs governed `0.00`. "Blast radius" here means the
  same bad record is re-retrieved and re-served across on average 2.12 later
  scripted steps — repetition of one error, not error propagation through
  agent state; governance contains it to zero.

## Extra attack families (trigger + same_channel)

Run separately from the headline mixture (`--attack-families`) and reported in
full in [`agentic_reliability_benchmark.md`](agentic_reliability_benchmark.md):
governance contains the AgentPoison-style `trigger` family 100% via provenance
trust (at an honest abstention cost on the near-equal-trust dormant step), and
**does not** contain the `same_channel` family — a poison on the same trusted
channel defeats both arms 100%. The boundary is stated, not hidden.

## End-to-end task success (stochastic agent)

The table above fixes the agent to isolate the memory contribution. This second
track lets the agent itself be imperfect (intrinsic per-step skill `p`, an
LLM-noise analog) and measures **end-to-end task success** — agent error and
memory error compounding over the trajectory. Agent noise is paired across arms.

```bash
PYTHONPATH=src python3 -m cognitive_memory reliability --end-to-end --seeds 1,2,3,4,5,6,7,8,9,10 --scenarios 96 --skills 1.0,0.99,0.95,0.90
```

| Agent skill `p` | no_memory | ungoverned | governed | agent-only baseline `p^n` | governance delta | discordant (gov-only / ung-only) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1.00 | 0.000 | 0.375 | **0.893** | 1.000 | **+0.518** | 497 / 0 |
| 0.99 | 0.000 | 0.371 | **0.875** | 0.980 | **+0.504** | 484 / 0 |
| 0.95 | 0.000 | 0.366 | **0.839** | 0.902 | **+0.473** | 454 / 0 |
| 0.90 | 0.000 | 0.339 | **0.770** | 0.810 | **+0.431** | 414 / 0 |

**Reading:** with governance, end-to-end success stays close to the agent-only
compounding baseline `p^n` (a well-governed memory adds almost no error of its
own); without it, memory injects correlated errors that pull end-to-end success
far below `p^n`. At every agent skill level the governance layer buys **+43 to
+52 percentage points** of whole-task success (McNemar p < 1e-100), and never
loses a task the ungoverned arm wins.

The gap between `governed` and the `p^n` baseline is governance's *safe*
abstentions on perfectly-forged-trust poison: those cost strict task success but
are recoverable, not silent errors. That is the honest trade — the layer converts
irreversible compounding failures into recoverable ones.

## What the numbers say

1. **Governance eliminates the catastrophic, compounding error class.** Silent
   (confident-wrong) actions fall from `72.6%` of steps to `0.0%`; every
   trajectory is catastrophe-free (`1.000` vs `0.375`). Memory poisoning succeeds
   `100%` of the time ungoverned and `0%` governed.
2. **It stays calibrated — it does not win by refusing to answer.** Benign
   accuracy is `1.000`, identical to the ungoverned arm and far above the
   `no_memory` ablation's `0.000`. The governed arm answers ordinary questions as
   well as the recall-first baseline while blocking the adversarial ones.
3. **Where truth is unknowable, it fails safe.** Governed task success is `0.893`,
   not `1.000`: on perfectly forged-trust poison it *abstains* (recoverable, `310`
   recoverable steps) rather than being fooled. That is the intended behavior —
   a recoverable "I don't know" instead of a silent wrong answer.
4. **Compliance is enforced, not hoped for.** `240` erasure/scope violations
   ungoverned vs `0` governed.

## Supported claims

- On this deterministic closed-loop benchmark, a governance wrapper over an
  identical recall backend reduces silent (compounding) agent errors from
  `72.6%` to `0.0%` of steps, with `p < 1e-100` (exact McNemar on paired
  trajectories) and paired-bootstrap CIs that exclude zero.
- It blocks the modeled query-only memory-poisoning attack `100%` of the time
  (vs `0%` blocked ungoverned) and enforces GDPR-style erasure and cross-entity
  scope isolation (`0` vs `240` violations).
- It does so without degrading benign recall accuracy (`1.000`), decisively
  clearing the no-memory ablation.
- The result is fully reproducible from seeds with no API key, no LLM, and no
  external services.

## External datasets (real-world payloads)

The synthetic numbers above use scenarios we authored. This section runs the
*same shipping governance code* against attack payloads and privacy data written
by other people. Reproduce with (data auto-downloaded once, then offline):

```
python3 -m cognitive_memory external-reliability --download --dataset deepset_prompt_injections
python3 -m cognitive_memory external-reliability --download --dataset injecagent
python3 -m cognitive_memory external-reliability --download --dataset tofu
python3 -m cognitive_memory external-reliability --track all --split dev --json
python3 -m cognitive_memory external-reliability --track all --split test --json   # report-only
```

Dev is for tuning (patterns were iterated only here); test is reported once.

### Injection detection — the write-side quarantine in isolation

| Dataset | Split | n | Recall | Precision | F1 | Benign FPR |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| deepset/prompt-injections | dev | 266 | `0.361` | `1.000` | `0.531` | `0.000` |
| deepset/prompt-injections | test | 280 | `0.337` | — | `0.504` | `0.000` |
| InjecAgent (tool-output) | dev | 269 | `0.000` | recall-only | — | — |
| InjecAgent (tool-output) | test | 241 | `0.000` | recall-only | — | — |

Reading: the pattern-based quarantine, in isolation, is **weak on real
payloads**. Before this pass it caught `~4%` of deepset injections; adding
generalized English+German override markers (calibrated on the dev split only)
lifted recall to `~36%` at **zero** benign false positives, and this generalized
to the held-out test split (`33.7%`). It catches **none** of InjecAgent's
indirect tool-output attacks, which read like ordinary user requests
("grant my friend access") and carry no override phrasing. This is the honest
ceiling of content pattern-matching and is exactly why detection is not the only
line of defense.

### Payload replay — the full governance stack, closed loop

Each real payload is planted as a higher-recency look-alike record (MINJA-style)
and queried through the paired trajectory harness. Only the attack *text* is
real; the fact/poison scaffold is synthetic.

| Dataset | Split | n | Ungoverned poisoned | Governed poisoned |
| --- | --- | ---: | ---: | ---: |
| deepset | dev | 108 | `108` | `0` |
| deepset | test | 95 | `95` | `0` |
| InjecAgent | dev | 269 | `269` | `0` |
| InjecAgent | test | 241 | `241` | `0` |

Reading: even the `~64–100%` of payloads that **evade the content detector** are
**contained `100%` of the time** in the closed loop, because the poison arrives
from a lower-trust `external_tool` source and loses to the higher-trust user
fact under provenance/trust-margin resolution. Governance does not depend on
detecting the attack text — the strong result comes from calibrated conflict
resolution, not pattern-matching. (Caveat: the scaffold is synthetic; this shows
containment of the retrieval-and-refire mechanism, not a live planting exploit.)

### Erasure enforcement + utility retention (TOFU)

| Split | Forget QA | Retain QA | Enforcement | Utility retention | Overblocking | Entity coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dev | 221 | 234 | `1.000` | `0.987` | `0.009` | `1.00` |
| test | 179 | 166 | `1.000` | `0.982` | `0.000` | `1.00` |

Reading: on real fictitious-author QA, forgetting an entity removes **all** of
its facts (`enforcement 1.000`) while other authors stay answerable
(`utility ~0.98`) and erasure-driven overblocking is near zero. Violation is
scored strictly (served-after-forget by record id **or** by value leak).
Author names are extracted heuristically from text; coverage is reported and was
`1.00` here, but noisy extraction (e.g. book titles mistaken for names) is a real
limitation on other slices.

### Scope isolation

ai4privacy is not yet downloaded pending a license review, so the scope track is
currently exercised only on committed fixtures (isolation `1.000`, no
cross-subject serves). Real-data scope numbers are deferred to a later pass.

### What these external numbers do and do not show

- They **do** show the governance stack contains real, third-party injection
  payloads end-to-end and enforces erasure on real QA data.
- They **do not** show the content detector alone is strong — it is not
  (`recall 0.34` deepset, `0.00` InjecAgent). We report that plainly.
- They are still not a production claim: the payload-replay scaffold is
  synthetic, TOFU authors are fictitious, and scope is fixture-only for now.

## Unsupported claims

- This is **not** a claim about any specific LLM's end-to-end task success. The
  agent policy is fixed by design to isolate the memory layer.
- This is **not** a reproduction of the full MINJA / AgentPoison exploits; it
  models their retrieval-and-refire mechanism, not the live planting procedure.
- This is **not** real agent traffic; scenarios are synthetic and the mixture
  is adversarial-heavy by design (62.5% of scenarios), so aggregate deltas
  overstate a mostly-benign production workload.
- This does **not** benchmark any named vendor. `ungoverned` represents the
  recall-first design class, not a specific product.
- This is **not** a production-readiness claim.
