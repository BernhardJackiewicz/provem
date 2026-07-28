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

- **Task-success McNemar:** governed-only wins `497`, ungoverned-only wins `0`,
  discordant `497`, **p = 4.9e-150**. Governed never loses a trajectory the
  ungoverned arm wins.
- **Per-step correctness gain:** `+0.549`, 95% CI `[+0.526, +0.572]` (excludes 0).
- **Silent-error-rate change:** `-0.726`, 95% CI `[-0.747, -0.705]` (excludes 0).
- **Blast radius (compounding):** ungoverned `2.12` silent errors per adversarial
  trajectory vs governed `0.00`. One bad memory corrupts every downstream step
  without governance; governance contains it to zero.

## End-to-end task success (stochastic agent)

The table above fixes the agent to isolate the memory contribution. This second
track lets the agent itself be imperfect (intrinsic per-step skill `p`, an
LLM-noise analog) and measures **end-to-end task success** — agent error and
memory error compounding over the trajectory. Agent noise is paired across arms.

```bash
PYTHONPATH=src python3 -m cognitive_memory reliability --end-to-end --seeds 1,2,3,4,5,6,7,8,9,10 --scenarios 96 --skills 1.0,0.99,0.95,0.90
```

| Agent skill `p` | no_memory | ungoverned | governed | agent-only baseline `p^n` | governance delta | McNemar p |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1.00 | 0.000 | 0.375 | **0.893** | 1.000 | **+0.518** | 4.9e-150 |
| 0.99 | 0.000 | 0.371 | **0.875** | 0.980 | **+0.504** | 4e-146 |
| 0.95 | 0.000 | 0.366 | **0.839** | 0.902 | **+0.473** | 4.3e-137 |
| 0.90 | 0.000 | 0.339 | **0.770** | 0.810 | **+0.431** | 4.7e-125 |

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

## Unsupported claims

- This is **not** a claim about any specific LLM's end-to-end task success. The
  agent policy is fixed by design to isolate the memory layer.
- This is **not** a reproduction of the full MINJA / AgentPoison exploits; it
  models their retrieval-and-refire mechanism, not the live planting procedure.
- This is **not** real agent traffic; scenarios are synthetic (though fair:
  benign-dominated, with a competent control arm).
- This does **not** benchmark any named vendor. `ungoverned` represents the
  recall-first design class, not a specific product.
- This is **not** a production-readiness claim.
