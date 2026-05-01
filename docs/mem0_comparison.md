# Mem0 Comparison Status

Last checked: 2026-05-02

## Status

Mem0 ran live against the local synthetic benchmark in Platform mode.

This is the first real Mem0 baseline result for this repository, but it remains
limited evidence:

- data is synthetic and local only,
- no real PII or real transcripts were used,
- the run used a temporary Python 3.11 environment because the system
  `python3` is still Python 3.9.6,
- Mem0 was evaluated as an optional external baseline, not as a mandatory
  dependency,
- no production-readiness or real-world superiority claim is supported.

## Environment Summary

The successful live run used:

- Mode: `platform`
- Runtime: Python `3.11.14` in a temporary virtual environment outside the repo
- Mem0 package: `mem0ai` installed in the temporary environment
- `MEM0_API_KEY`: present
- Dataset: existing synthetic benchmark suites only

No secret values were printed, copied into docs, or committed.

The default system runtime still cannot run Mem0 directly:

- System `python3`: `3.9.6`
- Mem0 dependency: not installed in the default environment

## Fairness Fixes

The first live structured run exposed a fairness issue: remote Mem0 memories
could bleed across benchmark scenarios when plain scenario `user_id` values were
reused.

The benchmark now scopes Mem0 calls with synthetic per-run and per-scenario
namespaces:

```text
engram_mem0_<suite>_<scenario_digest>:<scenario_user_id>
```

This prevents cross-scenario contamination without passing expected outputs,
labels, or benchmark answers to Mem0.

The fairness audit then exposed a second issue: Mem0 Platform writes returned
`PENDING` and simple searches could miss freshly written memories. The adapter
now:

- requests synchronous processing with `async_mode=False` when the SDK accepts
  it,
- waits briefly before reading after a queued write,
- deletes synthetic namespace memories on a best-effort basis when the SDK
  supports `delete_all`.

This is still not a tuned Mem0 integration. It is a minimal fairness guard for
write-before-read benchmarking.

## Run Command

The successful all-suite run was:

```bash
PYTHONPATH=src python -m cognitive_memory benchmark --suite all --include-mem0 --strict-optional
```

It was executed from the temporary Python 3.11 environment with local `.env`
loaded. The full Markdown report was generated outside the repo and is not
committed because it is generated benchmark output.

## Initial All-Suite Results

The first all-suite run below happened after namespace isolation but before the
write-settle audit. It is useful historical evidence that Mem0 ran live, but it
should not be used as the final comparison result without the sanity/audit
context below.

| System | Result | Accuracy | Structured | Noisy | Recruiting | Adversarial | p95 latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cognitive Memory Layer | `216/221` | `0.98` | `1.00` | `1.00` | `1.00` | `0.93` | `0.116 ms` |
| Mem0 external | `81/221` | `0.37` | `0.44` | `0.47` | `0.33` | `0.31` | `602.400 ms` |

Key governance fields from the same run:

| Metric | CML | Mem0 external | Note |
| --- | ---: | ---: | --- |
| current truth accuracy | `1.00` | `0.00` | Mem0 often returned no direct current answer |
| historical truth accuracy | `1.00` | `0.08` | Mem0 was not tuned for the harness's symbolic answers |
| obsolete memory usage rate | `0.00` | `0.02` | Lower is better |
| deleted memory leakage | `0.00` | `0.00` | Lower is better |
| do-not-use leakage | `0.00` | `0.00` | Lower is better |
| cross-project contamination | `0.00` | `0.00` | Lower is better |
| unsafe recall rate | `0.00` | `0.00` | Lower is better |
| prompt-injection memory success rate | `0.00` | `0.00` | Lower is better |
| ambiguous reference abstention | `1.00` | `1.00` | Higher is better |
| source conflict handling accuracy | `1.00` | `0.40` | Higher is better |
| abstention accuracy | `1.00` | `0.99` | Higher is better |
| provenance coverage | `1.00` | `1.00` | In this adapter, provenance exists only when exposed/derived from metadata |

## Fairness Audit Result

The current Mem0 sanity command is:

```bash
PYTHONPATH=src python -m cognitive_memory mem0-sanity --strict-optional
```

Current live result after write settling and best-effort cleanup:

| Suite | CML | Mem0 external | Mem0 p95 |
| --- | ---: | ---: | ---: |
| simple sanity | `12/12` | `6/12` | `2923.758 ms` |
| structured governance | `34/34` | `8/34` | `2825.504 ms` |

Mem0 audit metrics:

| Metric | Value |
| --- | ---: |
| simple_memory_accuracy | `0.5000` |
| mem0_retrieval_success_rate | `1.0000` |
| mem0_answer_format_mismatch_rate | `0.8333` |
| mem0_governance_gap_rate | `0.2308` |
| mem0_abstain_rate | `0.0000` |

An audited full all-suite rerun was attempted after these fairness fixes, but
Mem0 Platform stopped it with a quota/rate-limit error at the search endpoint:
the account had reached its monthly usage cap. Therefore the only complete
post-audit live result is the `mem0-sanity` simple + structured comparison
above. The initial all-suite `81/221` result remains historical/pre-audit
evidence and should not be used as a final audited comparison.

Interpretation:

- Mem0 does retrieve simple memories after write settling, so the adapter is not
  purely broken.
- Simple-memory accuracy is only `0.50`, so adapter/query/evaluator mismatch is
  still a material factor.
- Structured governance accuracy remains low (`8/34`) and failures include
  wrong current facts, policy leakage and answer-format mismatch.
- The original all-suite `81/221` result likely understated retrieval because
  of async write timing, but it also overstated governance safety because many
  passing policy cases were abstentions.

## Interpretation

The result supports only a narrow claim:

> The optional Mem0 Platform baseline can run live against the no-PII synthetic
> benchmark, but the current adapter/query setup is not yet strong enough for a
> clean superiority claim. On the audited simple sanity suite Mem0 scored
> `6/12`; on the audited structured governance suite it scored `8/34`.

It does not prove that CML is generally better than Mem0. The current harness is
shaped around governed symbolic memory behavior, policy leakage and scoped
abstention. Mem0 was run through a generic external-memory adapter and was not
tuned with a domain-specific prompt, custom extraction schema, or dedicated
policy layer.

## Unavailable or Derived Fields

Mem0 does not expose every governance field in the same way as CML.

- `selected_memories`: available when Mem0 search returns memory entries.
- `provenance`: available only when metadata includes source episode IDs or can
  be derived by the adapter.
- `abstention`: derived from empty/no usable search result; not a native Mem0
  abstention policy claim.
- deletion/do-not-use/scope/source-conflict semantics: evaluated by the local
  benchmark harness, not claimed as native Mem0 policy behavior.

Unavailable fields must not be treated as zero, success, or failure without
additional adapter support.

## Supported Claims

Supported:

- The Mem0 adapter path exists and is guarded behind optional setup.
- Mem0 Platform can run live against the local synthetic benchmark in a Python
  3.11 temporary environment.
- The live run used synthetic data only and did not include real PII.
- Mem0 benchmark calls now use synthetic per-run/per-scenario namespaces to
  avoid cross-scenario contamination.
- Mem0 Platform retrieves some simple memories after write settling.
- The current audited sanity result is Mem0 `6/12` on simple memory and `8/34`
  on structured governance.

Unsupported:

- CML superiority over Mem0 on real data.
- A clean CML-over-Mem0 product claim from the initial all-suite result.
- Production Mem0 integration readiness.
- Real-world recruiting usefulness.
- Native Mem0 support for this repo's deletion, do-not-use, provenance,
  abstention, scope isolation or source-conflict policies.
- Any result using real/anonymized datasets.

## Next Required Steps

1. Decide whether to tune the Mem0 baseline with an explicit schema/prompt before
   making stronger comparisons.
2. Re-run the all-suite Mem0 comparison after the write-settle change only after
   Mem0 quota is available again; expect it to be slower because writes are
   allowed to settle.
3. Re-run on external/anonymized validation data only after the data manifest and
   privacy workflow are approved.
4. Keep the default test and benchmark path dependency-free.
