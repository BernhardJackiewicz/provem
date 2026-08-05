# Agentic Memory Reliability Benchmark

This benchmark answers a single question with statistics rather than assertion:

> Does a governance layer over agent memory produce **significantly fewer
> catastrophic (silent, compounding) agent errors** than the same memory without
> governance: while still answering benign questions correctly?

It is the primary evidence artifact of this repository. Unlike a recall/QA
benchmark (LoCoMo, LongMemEval), it measures memory *inside a closed decision
loop* and scores the downstream action, not retrieval accuracy in isolation.

## Why this, and why now

Multi-step agents fail super-linearly: if each step succeeds with probability
`p`, an `n`-step task succeeds at best around `p^n`, and empirically *worse* than
that because step errors are positively correlated (a model conditions on its own
earlier mistakes). The reliability literature has moved to `pass^k` (all `k`
trials pass) precisely to expose this: e.g. a 90% per-step system drops to ~57%
at `k=8` [τ²-bench, https://github.com/sierra-research/tau2-bench;
"Beyond pass@1", https://arxiv.org/html/2603.29231v1].

Memory is a first-class source of compounding error, in two ways this benchmark
makes concrete:

1. **Read-side silent errors.** A memory that returns a wrong/stale/forbidden
   fact makes the agent act on a false premise. That error propagates. The
   fix that matters is *calibration*: answer when you reliably know, and abstain
   (a recoverable signal) instead of emitting a confident wrong answer.
2. **Write-side persistent corruption.** A single bad memory does not fail once:
   it re-fires on every future step that retrieves it. This is the mechanism
   behind published memory-poisoning attacks, which reach ~98% injection success
   with a query-only attacker [MINJA, https://arxiv.org/pdf/2503.03704] and
   >80% attack success with a single poisoned record
   [AgentPoison, https://arxiv.org/html/2407.12784].

A governance layer targets exactly these: calibrated abstention on the read side,
and quarantine / provenance-trust / erasure / scope isolation on the write side.

## Design: isolate the memory layer's causal contribution

The "agent brain" here is a **fixed deterministic policy**, not an LLM. This is
deliberate. If we used an LLM, its own reasoning noise (and prompt wording, and
sampling temperature) would swamp and confound the memory signal: the exact
problem that made prior memory-benchmark disputes irreproducible (the public
LoCoMo score fight between vendors turned on prompt tuning and single-run
variance [Zep vs Mem0, https://github.com/getzep/zep-papers/issues/5]).

By fixing the decision policy, any measured difference in action correctness is
attributable to the **memory layer**, not the LLM. The agent policy is trivial
and identical for all arms:

- call `memory.recall(query)`;
- if it returns a value → act on that value;
- if it abstains → take a safe fallback (ask / escalate) = a *recoverable* step.

### Three arms, identical inputs

| Arm | What it is |
| --- | --- |
| `no_memory` | Ablation: always abstains. Safe but useless. Proves the governed arm is *not* winning by abstaining on everything. |
| `ungoverned` | Recall-first memory: store everything, return the top similarity match, never abstain. A fair, competent baseline on benign recall. The control. |
| `governed` | The governance wrapper over the *same* backend. The treatment. |

### Fairness invariants (what makes this credible, not rigged)

- **Identical scenario stream, identical seeds** for every arm.
- **Identical recall substrate** (`NaiveBackend`, lexical top-k with recency
  tie-break) for `governed` and `ungoverned`. The only difference between them is
  the governance logic: nothing about recall power.
- **Identical inputs, including the governance-relevant utterances.** An erasure
  request ("please forget X") is fed to both arms. The governed arm interprets
  and enforces it; the ungoverned arm stores it as one more memory. Neither arm
  is handed a privileged signal the other lacks.
- **Outcome-based scoring against ground truth**, never the agent's self-report
  (the tau-bench / Terminal-Bench discipline).
- **All seeds reported; nothing hand-picked.** The suite is deterministic:
  the RNG is seeded (`random.Random(seed)`), so a given `(seed, scenarios)` reproduces exactly.

## Failure families (faithful, simplified analogs)

Each scenario is a short trajectory mixing benign recall with one injected
failure mode. The mixture is adversarial-heavy by design (to exercise the
governance paths): 62.5% of scenarios and 72.6% of steps are adversarial;
37.5% of scenarios are purely benign. This inflates the aggregate silent-error
and end-to-end deltas relative to a production workload, which would be mostly
ordinary recall: read the per-family results, not just the headline mix.

| Family | Models | What ungoverned does wrong |
| --- | --- | --- |
| `benign` | ordinary current-fact recall, incl. an update (supersession) | nothing: both arms should get these right |
| `poisoning` | MINJA-style query-only injection: an attacker writes a look-alike record that rewrites the victim's value | retrieves the poison (recency-biased) and repeats the wrong value on every downstream step |
| `injection` | prompt-injection-in-memory: a poisoned record carrying instruction content | stores and serves it |
| `erasure` | GDPR Art. 17 right-to-erasure: user asks to forget a fact | keeps serving the erased fact → compliance violation |
| `scope` | cross-entity / cross-tenant contamination | answers a query about entity A with entity B's look-alike record |

The attack analogs are deliberately *simplified but faithful*: a MINJA poison is
modeled as a look-alike record whose queryable tokens match the victim query but
whose value is rewritten, retrievable by the victim's own later queries: the
essential mechanism of the real attack, minus the LLM-elicitation and
progressive-shortening steps used to plant it in a live system.

### Extra attack families (reported separately from the headline mixture)

Two further families are run and reported on their own: they are **not** part
of the fixed headline mixture (so the headline numbers stay comparable across
runs). They answer two fair criticisms of the mixture above: that the poisoning
family's win could be a labeling artifact, and that a determined attacker on the
user's own channel might defeat provenance entirely. Run them with:

```bash
PYTHONPATH=src python3 -m cognitive_memory reliability --attack-families --seeds 1,2,3,4,5,6,7,8,9,10 --scenarios 40
```

| Family | Arm | Attack steps | Poison served | Contained | Benign (dormant) acc |
| --- | --- | ---: | ---: | ---: | ---: |
| `trigger` | ungoverned | 400 | 400 (1.000) | 0.000 | 400/400 (1.000) |
| `trigger` | **governed** | 400 | **0 (0.000)** | **1.000** | 246/400 (0.615) |
| `same_channel` | ungoverned | 1190 | 1190 (1.000) | 0.000 | - |
| `same_channel` | **governed** | 1190 | **1190 (1.000)** | **0.000** | - |

- **`trigger` (AgentPoison, arXiv:2407.12784)**: a poison on an *untrusted*
  channel that dominates retrieval only when a rare trigger phrase is present in
  the query, and lies dormant otherwise. Governance contains it **100%** (it
  never serves the poison value) by resolving on provenance trust, not by
  reading any label: so this is not the tautology the poisoning family could be
  accused of. The cost is honest: on the 40% of scenarios where the attacker
  forges *near-equal* trust, governance abstains on the dormant benign step too
  rather than coin-flip between two near-equal-trust values, which is why the
  dormant benign accuracy is 0.615 and not 1.0. Ungoverned keeps 100% dormant
  accuracy but is poisoned 100% of the time the trigger fires. That is the
  calibrated-abstention trade-off, shown, not hidden.
- **`same_channel` (the boundary)**: the MINJA poison arrives through the
  **same fully-trusted channel** as the user (`source="user"`, equal trust,
  written later), indistinguishable from a genuine update. Provenance/trust
  governance has no signal: latest-wins supersession serves the poison, so
  **both arms fail 100%**. This is the honest limit of a provenance layer:
  defending same-channel injection needs write-side content detection or human
  review (a different mechanism, only weakly exercised by the `injection`
  family), and we say so plainly rather than omit the case.

  One measured refinement: run with `--attack-families --lineage` to add a
  `governed_lineage` arm (`conflict_resolution="lineage"`) and a
  `same_channel_corroborated` family, where the true value carries assertions
  from two independent sources before the poison arrives. There the default
  arm already contains the poison but only by abstaining (the corroborating
  record makes the conflict cross-source); the lineage arm serves the
  corroborated true value instead, keeping utility at zero poison served. The
  equal-trust, no-history 1-vs-1 case remains uncontained in every arm:
  lineage does not solve it and we do not claim otherwise.

## Governance mechanisms under test

The governed wrapper (`GovernedMemory`, backend-agnostic) adds:

- **Write-side:** prompt-injection quarantine (never store instruction-like
  content as fact), sensitive-without-consent hold, provenance + source-trust
  tagging, and interpretation of erasure / do-not-use intents expressed in
  natural language.
- **Read-side:** erasure/do-not-use enforcement, entity-scope isolation,
  source-conflict resolution by provenance trust (clear trust margin → trusted
  first-party wins; otherwise abstain), a relevance floor (never answer a
  specific query from a weak name-only match), and calibrated abstention instead
  of a low-confidence or conflicted guess.

Supersession is respected: a newer value from the *same* source updates the old
one (not a conflict). Only cross-source disagreement without a clear trust margin
triggers abstention. This is what keeps benign accuracy at 1.0 rather than
collapsing into "abstain on everything."

## Metrics

Per step, the action is one of:

- **correct**: right value, or a correct abstention when the answer should be
  withheld (erased / forbidden / genuinely unknown);
- **silent_error**: a confident wrong action (the catastrophic, compounding
  failure);
- **recoverable_abstention**: "I don't know" when a value was expected: a miss,
  but recoverable and non-corrupting.

Aggregate metrics:

- **Task success**: strict: *all* steps in the trajectory correct (the
  `pass^k`-style outcome).
- **Catastrophe-free rate**: fraction of trajectories with *zero* silent errors.
- **Silent-error rate**: per-step rate of confident wrong actions.
- **Poisoning success rate**: fraction of adversarial steps where the attack
  changed the action.
- **Compliance violations**: count of steps that served erased or cross-scope
  data.
- **Benign accuracy**: accuracy on ordinary recall steps (the calibration
  guard).
- **Blast radius**: mean silent errors per adversarial trajectory: the honest,
  deterministic measure of compounding (one bad memory, how many corrupted
  steps).

## Statistics

All computed in dependency-free pure Python (`cognitive_memory.stats`) so a
reviewer can read the formula:

- **McNemar exact test** on paired per-trajectory success (governed vs
  ungoverned on the *same* scenarios). Per-task success is paired, so McNemar,
  not a two-proportion z-test, is the correct significance test.
- **Wilson score intervals** for every reported rate.
- **Paired bootstrap CIs** for continuous differences (per-step correctness gain,
  silent-error-rate change), resampling scenario indices jointly across arms to
  preserve pairing.

## End-to-end track: composing agent error with memory error

The isolation benchmark fixes the agent to attribute the effect cleanly to
memory. The **end-to-end track** answers the natural follow-up: *what happens to
whole-task success when the agent itself is imperfect?*

Here the agent is a `NoisyAgent` with an intrinsic per-step success probability
`p` (an LLM-reasoning-noise analog). Agent error and memory error now compound
over the trajectory. Two fairness details keep this honest:

- **Paired noise.** The agent's random draws are seeded per scenario and reused
  across arms, so `governed` and `ungoverned` see *identical* agent noise. The
  gap between them is still purely the memory-governance contribution.
- **Asking stays safe.** The agent errs only when it commits to an answer; when
  memory abstains it asks, and asking never becomes a hallucinated value. This
  does not punish governance for abstaining.

The reference point is the **agent-only compounding baseline** `p^n`: the success
a perfect memory would allow, limited only by the agent. With governance,
end-to-end success tracks close to `p^n` (memory adds ~0 error); without it,
memory injects correlated errors that pull success far below `p^n`. The
`memory-governance delta` = `governed - ungoverned` end-to-end success is the
reliability the layer buys at each agent skill.

```bash
PYTHONPATH=src python3 -m cognitive_memory reliability --end-to-end --skills 1.0,0.99,0.95,0.90
```

`NoisyAgent` implements the same `Agent` protocol as the deterministic policy, so
a real LLM agent can be dropped in for a live end-to-end run: that is the
optional, API-key-gated extension of this track and would be reported separately.

## Reproduce

```bash
PYTHONPATH=src python3 -m cognitive_memory reliability --seeds 1,2,3,4,5,6,7,8,9,10 --scenarios 96
PYTHONPATH=src python3 -m cognitive_memory reliability --end-to-end --seeds 1,2,3,4,5,6,7,8,9,10 --scenarios 96
PYTHONPATH=src python3 -m cognitive_memory reliability --json          # machine-readable headline
PYTHONPATH=src python3 -m unittest tests.test_reliability tests.test_stats
```

Same seeds → identical numbers. See `docs/reliability_results.md` for the current
canonical run.

## Honest limitations

- **Semantic erasure is opt-in and bounded.** By default, erasure matching is
  token-based; a paraphrase that shares no tokens with the erased term
  ("wants kids" erased, a note says "planning a family") is only caught when
  a semantic_erasure_threshold is set AND an embedding provider is injected.
  Even then it catches paraphrase SIMILARITY, not inference-level leakage (a
  rollup implying the erased fact in dissimilar words), it fails open with an
  audited error when the provider is down (token erasure stays the
  deterministic baseline), and erasures performed before the raw-term
  retention existed have no surface form to compare against.
- **The agent is a fixed policy, not an LLM.** This isolates the memory signal by
  design. It is therefore *not* a claim about any specific model's end-to-end
  task success; it is a claim about the memory layer's causal contribution to
  action correctness under a fixed policy. An LLM-in-the-loop extension is future
  work and would be reported separately.
- **The attack models are simplified analogs**, not the full published exploits.
  They capture the retrieval-and-refire mechanism, not the live planting
  procedure (MINJA's elicitation / progressive shortening, AgentPoison's
  gradient-guided trigger optimization).
- **Scenarios are synthetic and deterministic.** The mixture is adversarial-heavy
  (62.5% of scenarios) to exercise the governance paths, so the aggregate deltas
  overstate what a mostly-benign production workload would show; they are not
  real agent traffic. Read the per-family table for the per-attack picture.
- **The ungoverned baseline is a generic similarity memory**, not a specific
  tuned product. It is meant to represent the recall-first design class, not to
  benchmark any named vendor.
- A high score here is evidence that governance reduces a specific, important
  class of compounding failure under controlled conditions. It is not a claim of
  production readiness.
