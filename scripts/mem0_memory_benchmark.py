"""Fair, LLM-free head-to-head: OUR memory (Lager) vs Mem0's memory.

Metric = Answer-Recall@k in the retrieved context. For each LoCoMo question we
retrieve top-k from each memory and check whether the gold answer is recoverable
from the returned text, using ONE shared, order-independent token-set scorer
(fair to Mem0's paraphrasing, e.g. "May 7, 2023" vs gold "7 May 2023").

No LLM answerer on either side -> this isolates memory/retrieval strength (Lager),
not answer synthesis and not governance (Schloss). Same conversation, same
questions, same k, same scorer. That is the fairest apples-to-apples we can run.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, "src")

from cognitive_memory.locomo_eval import LoCoMoLoader, LoCoMoCognitiveSystem
from cognitive_memory.models import RetrievalRequest

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "at", "is", "are", "was", "were",
         "and", "or", "for", "with", "her", "his", "their", "it", "he", "she", "they",
         "did", "do", "does", "that", "this", "s", "by", "as", "be", "been"}


_NUMWORD = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
            "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
            "ten": "10", "eleven": "11", "twelve": "12"}


def content_tokens(text):
    """Normalize number words to digits so 'four years' credits gold '4 years'
    (fair to Mem0's paraphrasing, which tends to spell small numbers out)."""
    out = []
    for t in _WORD.findall(str(text).lower()):
        if t in _STOP:
            continue
        out.append(_NUMWORD.get(t, t))
    return out


def dated_content(episode):
    """Prepend the session date in LoCoMo answer style ('7 May 2023') so that
    timestamped dialogue is what BOTH memories ingest -- realistic and fair. The
    date lives structurally in episode.timestamp; neither system's raw turn text
    carries it, so without this every date question is unanswerable for both."""
    ts = getattr(episode, "timestamp", None)
    base = episode.content
    if ts is None:
        return base
    day = ts.day  # int -> '7' not '07', matches LoCoMo gold token
    stamp = "%d %s %d" % (day, ts.strftime("%B"), ts.year)
    return "%s | %s" % (stamp, base)


def is_date_answer(gold_answers):
    joined = " ".join(gold_answers).lower()
    if re.search(r"\b(19|20)\d{2}\b", joined):
        return True
    months = ("january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december")
    return any(m in joined for m in months)


def answer_recall(gold_answers, context_text):
    """Best fraction of gold-answer content tokens present in the retrieved context.

    Order-independent (a token set), so Mem0's reworded/reordered memories are
    credited. LoCoMo lists >=1 acceptable gold answer -> take the best-covered one.
    Returns (recall_fraction, is_full_hit)."""
    if isinstance(gold_answers, str):
        gold_answers = [gold_answers]
    ctx = set(content_tokens(context_text))
    best = None
    for ga in gold_answers:
        gold = set(content_tokens(ga))
        if not gold:
            continue
        frac = sum(1 for t in gold if t in ctx) / len(gold)
        if best is None or frac > best:
            best = frac
    if best is None:
        return None, None  # empty/degenerate gold -> skip
    return best, (best >= 0.999)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/external/locomo/locomo10.json")
    ap.add_argument("--conv-index", type=int, default=0)
    ap.add_argument("--limit-qa", type=int, default=None)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--user-id", default="engram_cmp_c0_dated")
    ap.add_argument("--no-dates", action="store_true", help="ingest raw turns without session dates")
    ap.add_argument("--skip-mem0", action="store_true")
    ap.add_argument("--reingest", action="store_true")
    ap.add_argument("--wait", type=int, default=90, help="seconds to wait for Mem0 async indexing")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    samples = LoCoMoLoader().load(args.data)
    sample = samples[args.conv_index]
    questions = list(sample.questions)
    # recall metric is meaningless for adversarial/unanswerable items -> exclude
    answerable = [q for q in questions if not q.expected_abstain]
    excluded = len(questions) - len(answerable)
    if args.limit_qa:
        answerable = answerable[: args.limit_qa]
    print("conv sample_id=%s  turns=%d  QA_total=%d  answerable=%d (excluded expected-abstain=%d)  eval=%d  top_k=%d"
          % (sample.sample_id, len(sample.episodes), len(questions), len(questions) - excluded,
             excluded, len(answerable), args.top_k), flush=True)

    use_dates = not args.no_dates
    print("date-augmented ingestion for BOTH sides: %s" % use_dates, flush=True)

    # ---------- OUR memory (recall-boost hybrid = our strongest Lager) ----------
    t0 = time.perf_counter()
    ours = LoCoMoCognitiveSystem(retrieval_mode="hybrid", recall_boost=True)
    for ep in sample.episodes:
        if use_dates:
            ep.content = dated_content(ep)
        ours.ingest(ep)
    print("OURS ingested %d episodes in %.1fs" % (len(sample.episodes), time.perf_counter() - t0), flush=True)

    def our_context(q):
        req = RetrievalRequest(query=q.question, user_id=sample.sample_id,
                               project_id="locomo",
                               task_type="temporal" if q.category_name == "temporal" else "general",
                               top_k=args.top_k)
        res = ours.retrieval.retrieve(req)
        texts = []
        for mem in res.selected_memories[: args.top_k]:
            rec = mem.to_dict()
            texts.append(ours._evidence_text_for(rec))
        return " \n ".join(t for t in texts if t)

    # ---------- Mem0 memory ----------
    mem0_ctx_fn = None
    if not args.skip_mem0:
        from mem0 import MemoryClient
        client = MemoryClient(api_key=os.environ["MEM0_API_KEY"])
        # ingest per session as a conversation (gives Mem0 context = fair to Mem0)
        existing = 0
        try:
            existing = len(client.get_all(version="v2", filters={"user_id": args.user_id}, page_size=1) or [])
        except Exception:
            pass
        conv = sample  # episodes already flattened; rebuild per-session grouping
        if args.reingest or existing == 0:
            # group episodes by session prefix of dia_id ("D<session>:<turn>")
            sessions = {}
            for ep in sample.episodes:
                sid = str(getattr(ep, "id", "") or "").split(":")[0]
                sessions.setdefault(sid, []).append(ep)
            n_add = 0
            for sid, eps in sessions.items():
                msgs = []
                for i, ep in enumerate(eps):
                    role = "user" if i % 2 == 0 else "assistant"
                    msgs.append({"role": role, "content": ep.content})
                try:
                    client.add(msgs, user_id=args.user_id, version="v2")
                    n_add += 1
                except Exception as e:
                    print("  mem0 add FAIL (%s): %s" % (sid, str(e)[:120]), flush=True)
            print("MEM0 added %d session-batches; waiting %ds for async indexing..." % (n_add, args.wait), flush=True)
            time.sleep(args.wait)
        else:
            print("MEM0 already has memories for user_id=%s (skip ingest)" % args.user_id, flush=True)

        def mem0_context(q):
            try:
                res = client.search(q.question, version="v2",
                                    filters={"user_id": args.user_id}, top_k=args.top_k)
            except Exception as e:
                return "__MEM0_ERROR__:" + str(e)[:160]
            items = res if isinstance(res, list) else res.get("results", res.get("memories", []))
            texts = []
            for it in (items or [])[: args.top_k]:
                if isinstance(it, dict):
                    texts.append(str(it.get("memory") or it.get("text") or it.get("content") or ""))
                else:
                    texts.append(str(it))
            return " \n ".join(t for t in texts if t)
        mem0_ctx_fn = mem0_context

    # ---------- score both on identical questions ----------
    rows = []
    agg = {"all": {"n": 0, "our_hit": 0, "our_rec": 0.0, "m0_hit": 0, "m0_rec": 0.0, "m0_n": 0},
           "date": {"n": 0, "our_hit": 0, "our_rec": 0.0, "m0_hit": 0, "m0_rec": 0.0, "m0_n": 0},
           "nondate": {"n": 0, "our_hit": 0, "our_rec": 0.0, "m0_hit": 0, "m0_rec": 0.0, "m0_n": 0}}
    mem0_errors = 0
    for q in answerable:
        gold = q.answers
        our_ctx = our_context(q)
        our_frac, our_hit = answer_recall(gold, our_ctx)
        if our_frac is None:
            continue
        bucket = "date" if is_date_answer(gold) else "nondate"
        for key in ("all", bucket):
            agg[key]["n"] += 1
            agg[key]["our_rec"] += our_frac
            agg[key]["our_hit"] += 1 if our_hit else 0
        row = {"question": q.question, "gold": " | ".join(map(str, gold)), "category": q.category_name,
               "date_answer": bucket == "date",
               "our_recall": round(our_frac, 3), "our_hit": bool(our_hit)}
        if mem0_ctx_fn is not None:
            m0_ctx = mem0_ctx_fn(q)
            if m0_ctx.startswith("__MEM0_ERROR__"):
                mem0_errors += 1
                row["mem0_error"] = m0_ctx[15:]
            else:
                m0_frac, m0_hit = answer_recall(gold, m0_ctx)
                for key in ("all", bucket):
                    agg[key]["m0_rec"] += m0_frac
                    agg[key]["m0_hit"] += 1 if m0_hit else 0
                    agg[key]["m0_n"] += 1
                row["mem0_recall"] = round(m0_frac, 3)
                row["mem0_hit"] = bool(m0_hit)
        rows.append(row)
        if agg["all"]["n"] % 25 == 0:
            print("  ...%d scored" % agg["all"]["n"], flush=True)
    n = agg["all"]["n"]
    our_hits = agg["all"]["our_hit"]; our_recall_sum = agg["all"]["our_rec"]
    m0_hits = agg["all"]["m0_hit"]; m0_recall_sum = agg["all"]["m0_rec"]

    print("\n================ RESULT (n=%d answerable QA, top_k=%d) ================" % (n, args.top_k))
    print("OURS  full-answer-hit@%d = %.3f (%d/%d)   mean token-recall = %.3f"
          % (args.top_k, our_hits / n, int(our_hits), n, our_recall_sum / n))
    if mem0_ctx_fn is not None:
        m0_n = n - mem0_errors
        if m0_n > 0:
            print("MEM0  full-answer-hit@%d = %.3f (%d/%d)   mean token-recall = %.3f%s"
                  % (args.top_k, m0_hits / m0_n, int(m0_hits), m0_n, m0_recall_sum / m0_n,
                     "" if mem0_errors == 0 else "   [%d mem0 search errors excluded]" % mem0_errors))
            print("\nDELTA (ours - mem0): full-hit %+.3f pts | mean-recall %+.3f"
                  % (our_hits / n - m0_hits / m0_n, our_recall_sum / n - m0_recall_sum / m0_n))
            print("\n-- breakdown --")
            for key in ("nondate", "date"):
                a = agg[key]
                if a["n"] == 0:
                    continue
                mn = a["m0_n"] or 1
                print("  %-8s n=%3d | OURS hit=%.3f rec=%.3f | MEM0 hit=%.3f rec=%.3f | delta-hit %+.3f"
                      % (key, a["n"], a["our_hit"] / a["n"], a["our_rec"] / a["n"],
                         a["m0_hit"] / mn, a["m0_rec"] / mn, a["our_hit"] / a["n"] - a["m0_hit"] / mn))
        else:
            print("MEM0  no successful searches (%d errors) -- quota/connectivity" % mem0_errors)

    if args.out:
        json.dump({"sample_id": sample.sample_id, "n": n, "top_k": args.top_k,
                   "our_hit_rate": our_hits / n, "our_mean_recall": our_recall_sum / n,
                   "mem0_hit_rate": (m0_hits / (n - mem0_errors)) if (mem0_ctx_fn and n - mem0_errors > 0) else None,
                   "mem0_mean_recall": (m0_recall_sum / (n - mem0_errors)) if (mem0_ctx_fn and n - mem0_errors > 0) else None,
                   "mem0_errors": mem0_errors, "rows": rows}, open(args.out, "w"), indent=2)
        print("\nwrote", args.out)


if __name__ == "__main__":
    main()
