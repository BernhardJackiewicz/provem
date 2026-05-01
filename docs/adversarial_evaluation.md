# Adversarial Evaluation

## Why This Exists

The structured, noisy and recruiting suites can now all pass in the local
prototype. That does not prove robust memory. It means the synthetic tests have
become shaped around the current deterministic implementation.

The adversarial suite is intended to find failures, not to preserve a perfect
score.

## Current Coverage

The `adversarial` suite contains 75 synthetic multi-session scenarios. It covers:

- same candidate name across different clients or projects
- same company used as client and past employer
- overlapping candidate, client and role names
- role changes mid-conversation
- stale facts with newer contradictory facts
- vague deletion requests
- malicious or prompt-injection-like memory content
- false corrections and tool/source conflicts
- ambiguous pronouns and "that one" references
- multiple possible antecedents
- sensitive information hidden inside irrelevant notes
- project switching inside one paragraph
- recruiter notes versus candidate/client statements
- do-not-contact versus do-not-mention distinctions
- historical questions where old facts are correct
- current questions where old facts must not be used
- abstention-required cases
- relationship-heavy candidate/client/role contexts

## Mutation Testing

The suite also creates deterministic scenario mutations:

- entity renames
- candidate/client/project id swaps
- template paraphrase variants
- inserted distractor facts
- reordered sessions
- inserted outdated conflicting facts
- irrelevant sensitive facts

`mutation_stability` reports how often mutated scenarios still pass. It is not a
real paraphrase-robustness guarantee, but it prevents the benchmark from being
only one fixed set of names and event orderings.

## Metrics

Adversarial-specific metrics:

- `adversarial_accuracy`
- `mutation_stability`
- `unsafe_recall_rate`
- `prompt_injection_memory_success_rate`
- `stale_fact_resurrection_rate`
- `ambiguous_reference_abstention_rate`
- `source_conflict_handling_accuracy`

These metrics are intentionally harsh. A lower adversarial score can be more
useful than a perfect synthetic score because it identifies where architecture
work is still needed.

## Current Failure Meaning

Before the hardening pass, Engram / the Cognitive Memory Layer scored 41/63 on the
adversarial suite with high unsafe recall and no source-conflict handling. The
hardening pass added source metadata, source conflict handling, memory
quarantine, mixed-scope identity abstention and out-of-order event handling.

Current local result after adversarial hardening plus local event-model
groundwork:

- `adversarial`: 70/75
- `unsafe_recall_rate`: 0.0000
- `prompt_injection_memory_success_rate`: 0.0000
- `stale_fact_resurrection_rate`: 0.0000
- `ambiguous_reference_abstention_rate`: 1.0000
- `source_conflict_handling_accuracy`: 1.0000
- `abstention_accuracy`: 1.0000

Remaining useful failure areas:

- Some identity collisions are now resolved when structured events clearly link
  candidate facts to a specific client context.
- Project switching inside one paragraph is still missed by the deterministic
  extractor.
- Renamed mutation cases can still safe-abstain when the mutation creates a
  query/entity mismatch.
- Template paraphrases such as "two weeks" versus `2_weeks` are still extractor
  misses.
- The local event model is additive and deterministic. It is useful Graphiti
  groundwork, not a real temporal graph integration.
- The source trust model is lightweight and deterministic; it is not a real
  verification workflow.
- Prompt-injection-like content and hidden sensitive facts are still detected
  by local patterns, not comprehensive classifiers.

These are MVP 1 limitations. They should not be fixed by hardcoding scenario
strings. Any fix should be architecture-level: source trust, stronger identity
resolution, safer extraction, explicit policy state or better abstention.

## Freeze Candidate Interpretation

For the MVP 1 freeze candidate, `70/75` adversarial accuracy is acceptable only
because the remaining failures are documented as safe abstentions, extraction
misses or mutation artifacts. A future change that raises adversarial accuracy
by weakening abstention, source-conflict handling or forbidden-memory filtering
should be treated as a regression.

The next meaningful improvement is not another hand-tuned synthetic rule. It is
validation against real or anonymized transcripts, stronger mutation coverage,
or a live external baseline comparison.

## Interpretation

Passing `structured`, `noisy` and `recruiting` is necessary but not sufficient.
The adversarial suite is the current signal that this remains a local synthetic
research prototype, not a production memory system.
