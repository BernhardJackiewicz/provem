# Our memory (Lager) vs Mem0's memory — retrieval-recall head-to-head

> **Read this alongside `mem0_locomo_e2e_results.md`, which is the authoritative
> answer.** This page measures *retrieval recall* only (a proxy that favours
> verbatim storage). The full **end-to-end** benchmark (retrieve → LLM answers →
> LLM judges, all 10 conversations, 1986 QA) reverses the impression here:
> **Mem0 wins, 0.475 vs 0.388 on answerable questions, p ≈ 5×10⁻⁹.** When a real
> LLM must produce the answer, Mem0's distilled memories beat our verbose raw turns.

**Question asked:** which memory is stronger, and by exactly how much?

**Short answer:** on a like-for-like *retrieval-recall* metric, the two are
**roughly comparable once Mem0 is measured fairly** — a tie on one conversation,
~1.6× in our favour on the other, ~1.2× in our favour pooled. An initial run
made us look ~1.9× stronger, but that margin was a **measurement artifact** of
Mem0's slow async indexing plus a shallow top-k, not a real memory advantage.
Crucially, this metric does **not** measure Mem0's designed strength (an LLM
answering over its curated memories), which we could not test here.

## What was measured, and why this metric

- **Metric — Answer-Recall@k.** For each LoCoMo question, retrieve the top-k
  units from each memory and check whether the gold answer is recoverable from
  the returned text. Scoring is an **order-independent token-set recall** with
  number-word normalization (`four`↔`4`), so Mem0's paraphrases (`"May 7, 2023"`
  for gold `"7 May 2023"`) are credited. Two numbers per system: `full-hit`
  (all gold answer tokens present) and `mean token-recall` (fraction present).
- **Why this and not end-to-end accuracy.** Answering a question from retrieved
  memories needs an LLM. The OpenAI key used for the LLM-answerer work was
  revoked, so end-to-end QA (Mem0's home turf, and where published LoCoMo
  J-scores come from) was **out of scope**. Retrieval recall is the honest,
  LLM-free proxy for raw memory strength: *does the store bring back the answer?*
- **Fair to both.** Same conversations, same questions, same k, same scorer.
  Session dates were prepended to every turn in LoCoMo answer format
  (`"7 May 2023 | Speaker: ..."`) **identically for both** — without this every
  date question is unanswerable for both (the date lives in session metadata,
  not turn text). Adversarial / "no information" questions (expected-abstain)
  are excluded; they test governance, not recall.
- **Sample.** Two LoCoMo conversations (`conv-26`, `conv-30`), 233 answerable
  QA total, bounded by Mem0 Platform quota/cost — a representative sample, not
  the full 10-conversation set.

## Results

### Fair conditions — Mem0 store settled, top_k=20 (headline)

| Conversation | n | Ours full-hit | Mem0 full-hit | Ours mean-recall | Mem0 mean-recall |
|---|---|---|---|---|---|
| conv-26 | 152 | **0.309** | 0.316 | 0.579 | 0.567 |
| conv-30 | 81 | **0.481** | 0.300 | 0.661 | 0.539 |
| **pooled** | **233** | **0.369** | **0.310** | **0.607** | **0.557** |

Pooled: **+0.059 full-hit in our favour (~1.19×)**, +0.050 mean-recall. But it is
**condition-dependent**: `conv-26` is a statistical tie (Mem0 +0.007 on full-hit,
we +0.012 on recall); `conv-30` we lead +0.181 (~1.6×).

### Naive conditions — Mem0 freshly ingested, top_k=10 (the artifact)

| Conversation | n | Ours full-hit | Mem0 full-hit |
|---|---|---|---|
| conv-26 | 152 | 0.230 | 0.125 |
| conv-30 | 81 | 0.395 | 0.198 |
| **pooled** | **233** | **0.288** | **0.150** |

This looks like **~1.9× in our favour** — and it is misleading. Two reasons Mem0
was understated here (both fixed in the fair run above):

1. **Mem0's async indexing is slow and kept growing.** For `conv-26` the store
   went 39 → 95 → 146 extracted memories *over the course of the session*,
   long after `add()` returned. Searching 30 s after ingest queried a
   half-built store. Concrete miss that later resolved: "What did Caroline
   research?" (gold *Adoption agencies*) scored 0 at benchmark time; minutes
   later Mem0's top memory was literally *"Caroline is researching adoption
   agencies…"*.
2. **top-10 was too shallow for Mem0.** Its **extraction ceiling** — the answer
   present *anywhere* in its whole store — was 0.408 (conv-26) / 0.272 (conv-30)
   full-hit, well above what top-10 surfaced. Raising to top-20 recovered most
   of that gap on conv-26.

## Honest reading

- **We are not dramatically stronger.** The believable gap is small: a tie to
  ~1.2× on retrieval recall pooled, driven mostly by one conversation.
- **Where our edge is real:** *date / verbatim-detail* questions. On `conv-30`
  date answers we hit 0.840 vs Mem0 0.300 — because we store turns **verbatim**
  and preserve the exact date, whereas Mem0's LLM extraction sometimes drops or
  mis-resolves it (it fabricated *"2026-07-29"*, the real current date, for a
  2023 event). This is a genuine architectural trade-off: **verbatim + lexical
  retrieval preserves detail; extractive compression generalizes but loses
  specifics.**
- **Where Mem0's edge is real (and unmeasured here):** it distills a
  conversation to a compact, deduplicated, semantically-searchable memory set
  (30–146 items for a whole conversation) built to be *read by an LLM*. Its
  published LoCoMo J-scores (~0.5–0.66, with an LLM answerer + LLM judge) come
  from that end-to-end pipeline and are **not comparable** to the retrieval
  metric here.
- **The metric mildly favours us.** Token-recall rewards raw answer text being
  present, which verbatim storage gives for free; extractive storage is
  penalized when it rewords or omits. Read the numbers as "raw answer presence
  in retrieval", not "answer quality".

## Threats to validity

- Two conversations, 233 QA — representative, not exhaustive.
- Retrieval recall ≠ end-to-end answer accuracy (no LLM answerer this run).
- Mem0 async indexing is non-deterministic in timing; its numbers are a
  point-in-time snapshot and were still rising at report time.
- Token-set scoring is lenient on order/paraphrase but strict on token identity
  (a synonym is a miss for both sides).

## Reproduce

```
# requires a Python 3.11 venv with `mem0ai`; MEM0_API_KEY from the environment,
# never written to a file.
export MEM0_API_KEY=...           # your key
python scripts/mem0_memory_benchmark.py --conv-index 0 --top-k 20 --wait 120 \
    --user-id engram_cmp_c0 --reingest --out /tmp/conv0.json
```

Fresh ingestion is slow-async on Mem0's side; `--wait` covers indexing, but the
store may keep growing — re-run scoring (drop `--reingest`) after a few minutes
for a settled number.
