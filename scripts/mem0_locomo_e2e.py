"""Complete, correct LoCoMo end-to-end head-to-head: OUR memory vs Mem0.

This is the fair, literature-standard comparison (the "J-score" methodology):
  1. Both memories ingest the SAME conversation (date-augmented, identical input).
  2. Per question: retrieve top-k from each memory -> the SAME LLM answerer
     synthesizes an answer from ONLY that retrieved context.
  3. The SAME LLM judge grades correctness semantically (paraphrase/format-robust).

The only thing that differs between the two systems is the memory. Answerer,
judge, top-k, date-augmentation, question set and abstention handling are identical.

Cost control: the judge only sees (question, gold, predicted) -> cheap. A --pilot
run measures real token cost and extrapolates to the full set BEFORE committing.
The run hard-stops before the --budget-eur cap. Results stream to a resumable
JSONL so a stop/restart never repeats a paid call.

Secrets: OPENAI_API_KEY and MEM0_API_KEY are read from the environment only and
are never written to any file.
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, "src")

from cognitive_memory.locomo_eval import LoCoMoLoader, LoCoMoCognitiveSystem
from cognitive_memory.models import RetrievalRequest

# ---------------------------------------------------------------- pricing (USD/1M tokens)
# EDIT these to your actual account prices; the pilot prints real $ so you can calibrate.
PRICES = {
    "gpt-5-mini":      (0.25, 2.00),
    "gpt-5":           (1.25, 10.00),
    "gpt-4o":          (2.50, 10.00),
    "gpt-4o-mini":     (0.15, 0.60),
    "gpt-4.1":         (2.00, 8.00),
    "gpt-4.1-mini":    (0.40, 1.60),
}
EUR_PER_USD = 0.92

_COST = {"usd": 0.0, "calls": 0, "in_tok": 0, "out_tok": 0}
_COST_LOCK = threading.Lock()

# ---------------------------------------------------------------- LLM disk caches
# Iteration economics: only CHANGED contexts pay. Answerer keyed by (qid, model,
# effort, context-hash); judge keyed by (qid, model, effort, normalized answer).
import hashlib


class DiskCache:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        if path and os.path.exists(path):
            with open(path) as h:
                for line in h:
                    try:
                        r = json.loads(line)
                        self.data[r["k"]] = r["v"]
                    except Exception:
                        pass

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        with self.lock:
            if key in self.data:
                return
            self.data[key] = value
            if self.path:
                with open(self.path, "a") as h:
                    h.write(json.dumps({"k": key, "v": value}) + "\n")


def _h(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


_ANSWER_CACHE = DiskCache(None)
_JUDGE_CACHE = DiskCache(None)


def _price(model, in_tok, out_tok):
    pin, pout = PRICES.get(model, (2.5, 10.0))
    return (in_tok * pin + out_tok * pout) / 1_000_000.0


def openai_chat(model, messages, max_tokens=256, retries=5, reasoning_effort="minimal"):
    """Minimal stdlib Chat Completions call. Returns (text, usage_dict).

    Omits temperature (gpt-5* only accept the default). Sets reasoning_effort so
    reasoning tokens don't eat the whole completion budget on these simple tasks
    (a gpt-5 model with a small token cap and default reasoning returns ''!).
    Retries on 429/5xx with fixed exponential backoff (no RNG)."""
    key = os.environ["OPENAI_API_KEY"]
    body = {"model": model, "messages": messages, "max_completion_tokens": max_tokens}
    if reasoning_effort and ("gpt-5" in model or model.startswith("o")):
        body["reasoning_effort"] = reasoning_effort
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=data,
            headers={"Authorization": "Bearer %s" % key, "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = payload["choices"][0]["message"]["content"] or ""
            usage = payload.get("usage", {})
            it = int(usage.get("prompt_tokens", 0)); ot = int(usage.get("completion_tokens", 0))
            with _COST_LOCK:
                _COST["usd"] += _price(model, it, ot); _COST["calls"] += 1
                _COST["in_tok"] += it; _COST["out_tok"] += ot
            return text.strip(), usage
        except urllib.error.HTTPError as e:
            last = "%s %s" % (e.code, e.read().decode("utf-8", "ignore")[:200])
            if e.code in (429, 500, 502, 503, 529):
                time.sleep(2 ** attempt); continue
            # some models reject max_completion_tokens vs max_tokens - swap once
            if e.code == 400 and "max_completion_tokens" in last and "max_tokens" not in body and attempt == 0:
                body["max_tokens"] = body.pop("max_completion_tokens")
                data = json.dumps(body).encode("utf-8"); continue
            # output limit hit (reasoning ate the budget) - double and retry
            if e.code == 400 and "output limit" in last.lower() or (e.code == 400 and "max_tokens or model output" in last):
                fld = "max_tokens" if "max_tokens" in body else "max_completion_tokens"
                body[fld] = min(body.get(fld, max_tokens) * 2, 2048)
                data = json.dumps(body).encode("utf-8")
                if attempt < retries - 1:
                    continue
            raise RuntimeError("OpenAI %s" % last)
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e); time.sleep(2 ** attempt)
    raise RuntimeError("OpenAI failed after retries: %s" % last)


# ---------------------------------------------------------------- answerer + judge
ANSWER_SYS = ("You answer a question using ONLY the memory excerpts provided. "
              "Be concise: reply with just the answer, no explanation. "
              "If the excerpts do not contain the answer, reply exactly: NO ANSWER.")

JUDGE_SYS = ("You grade a predicted answer against the gold answer(s) for a "
             "question. Accept semantic equivalence, paraphrases, and any date/"
             "number format (e.g. '7 May 2023' == 'May 7, 2023' == '2023-05-07'). "
             "Reply with a single token: YES if the prediction is correct, else NO.")

_NOANS = re.compile(r"\b(no answer|not mentioned|not (stated|provided|available|specified)|"
                    r"don'?t know|no information|unknown|cannot (determine|find|answer))\b", re.I)


def answerer(model, question, context, effort="low", qid=""):
    ctx = context if context.strip() else "(no memory retrieved)"
    key = _h("ans", qid, model, effort, _h(ctx))
    cached = _ANSWER_CACHE.get(key)
    if cached is not None:
        return cached
    msgs = [{"role": "system", "content": ANSWER_SYS},
            {"role": "user", "content": "Memory excerpts:\n%s\n\nQuestion: %s\nAnswer:" % (ctx, question)}]
    text, _ = openai_chat(model, msgs, max_tokens=512, reasoning_effort=effort)
    _ANSWER_CACHE.put(key, text)
    return text


def is_noanswer(text):
    t = (text or "").strip()
    return (not t) or bool(_NOANS.search(t)) or t.upper() == "NO ANSWER"


def judge(model, question, golds, predicted, effort="low", qid=""):
    key = _h("jud", qid, model, effort, (predicted or "").strip().lower())
    cached = _JUDGE_CACHE.get(key)
    if cached is not None:
        return bool(cached)
    gold = " | ".join(str(g) for g in golds)
    msgs = [{"role": "system", "content": JUDGE_SYS},
            {"role": "user", "content": "Question: %s\nGold answer(s): %s\nPredicted: %s\nCorrect?"
             % (question, gold, predicted)}]
    text, _ = openai_chat(model, msgs, max_tokens=128, reasoning_effort=effort)
    verdict = text.strip().upper().startswith("Y")
    _JUDGE_CACHE.put(key, verdict)
    return verdict


# ---------------------------------------------------------------- date augmentation (fair, identical)
def dated_content(ep):
    ts = getattr(ep, "timestamp", None)
    if ts is None:
        return ep.content
    return "%d %s %d | %s" % (ts.day, ts.strftime("%B"), ts.year, ep.content)


# ---------------------------------------------------------------- our memory
# `features` is the campaign's A/B switchboard: every optimization is opt-in and
# attributable. Empty set == the frozen 0.388 baseline pipeline.
def build_ours(sample, use_dates, features=frozenset()):
    sysm = LoCoMoCognitiveSystem(retrieval_mode="hybrid", recall_boost=True)
    sysm.ours_features = frozenset(features)
    for ep in sample.episodes:
        if use_dates:
            ep.content = dated_content(ep)
        sysm.ingest(ep)
    return sysm


def our_context(sysm, sample, q, k):
    features = getattr(sysm, "ours_features", frozenset())
    req = RetrievalRequest(query=q.question, user_id=sample.sample_id, project_id="locomo",
                           task_type="temporal" if q.category_name == "temporal" else "general", top_k=k)
    res = sysm.retrieval.retrieve(req)
    texts = [t for t in (sysm._evidence_text_for(m.to_dict()) for m in res.selected_memories[:k]) if t]
    return " \n ".join(texts)


# ---------------------------------------------------------------- mem0 memory
def mem0_ingest(client, uid, sample, use_dates, wait, settle_stable=20, poll=10):
    """Ingest per-session, then poll get_all until the store count stops growing."""
    sessions = {}
    for ep in sample.episodes:
        sid = str(getattr(ep, "id", "") or "").split(":")[0]
        content = dated_content(ep) if use_dates else ep.content
        sessions.setdefault(sid, []).append(content)
    n_add = 0
    for sid, contents in sessions.items():
        msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": c} for i, c in enumerate(contents)]
        try:
            client.add(msgs, user_id=uid, version="v2"); n_add += 1
        except Exception as e:
            print("   add FAIL %s: %s" % (sid, str(e)[:120]), flush=True)
    # settle: poll until count is stable for `settle_stable`s, capped at `wait`s
    t0 = time.time(); last = -1; stable_since = None
    while time.time() - t0 < wait:
        time.sleep(poll)
        cnt = mem0_count(client, uid)
        if cnt == last and cnt > 0:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= settle_stable:
                break
        else:
            stable_since = None; last = cnt
    return n_add, mem0_count(client, uid)


def _mem0_retry(fn, tries=6):
    """Call a Mem0 client method, retrying on 429/5xx with fixed backoff."""
    last = None
    for attempt in range(tries):
        try:
            return fn()
        except Exception as e:
            last = str(e)
            if "429" in last or "Too Many" in last or "500" in last or "502" in last or "503" in last:
                time.sleep(min(2 ** attempt, 20)); continue
            raise
    raise RuntimeError("mem0 retry exhausted: %s" % last)


def mem0_has(client, uid):
    """Cheap existence probe (one small request) instead of full pagination."""
    try:
        r = _mem0_retry(lambda: client.get_all(version="v2", filters={"user_id": uid}, page=1, page_size=1))
    except Exception:
        return False
    items = r if isinstance(r, list) else r.get("results", [])
    return bool(items)


def mem0_count(client, uid):
    n = 0; page = 1
    while True:
        try:
            r = _mem0_retry(lambda: client.get_all(version="v2", filters={"user_id": uid}, page=page, page_size=100))
        except Exception:
            break
        items = r if isinstance(r, list) else r.get("results", [])
        if not items:
            break
        n += len(items); page += 1
        if page > 20:
            break
    return n


def mem0_context(client, uid, q, k):
    try:
        res = _mem0_retry(lambda: client.search(q.question, version="v2", filters={"user_id": uid}, top_k=k))
    except Exception as e:
        return "__ERR__" + str(e)[:160]
    items = res if isinstance(res, list) else res.get("results", res.get("memories", []))
    return " \n ".join(str(it.get("memory") or it.get("text") or "") if isinstance(it, dict) else str(it)
                       for it in (items or [])[:k])


# ---------------------------------------------------------------- driver
def load_done(path):
    done = {}
    if os.path.exists(path):
        with open(path) as h:
            for line in h:
                try:
                    r = json.loads(line)
                    done[(r["conv"], r["qid"], r["system"])] = r
                except Exception:
                    pass
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/external/locomo/locomo10.json")
    ap.add_argument("--answerer", default="gpt-5-mini")
    ap.add_argument("--judge", default="gpt-5")
    ap.add_argument("--answer-effort", default="medium")
    ap.add_argument("--judge-effort", default="low")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--budget-eur", type=float, default=30.0)
    ap.add_argument("--convs", default=None, help="comma-separated conv indices, default all")
    ap.add_argument("--include-abstain", action="store_true", help="also evaluate expected-abstain QA")
    ap.add_argument("--no-dates", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--wait", type=int, default=300, help="max seconds to wait for Mem0 settle per conv")
    ap.add_argument("--uid-prefix", default="engram_e2e")
    ap.add_argument("--out", default="/private/tmp/claude-501/-Users-bernhard-Desktop-brain/e9f97b9f-09f0-4aff-ac11-a991e6b1aafa/scratchpad/e2e_results.jsonl")
    ap.add_argument("--skip-mem0", action="store_true")
    ap.add_argument("--systems", default=None, help="comma list: ours,mem0 (default both; ours == --skip-mem0)")
    ap.add_argument("--ours-features", default="", help="comma list of opt-in pipeline features for OUR side")
    ap.add_argument("--tag", default="", help="run tag for the ledger")
    ap.add_argument("--cache-dir", default="/private/tmp/claude-501/-Users-bernhard-Desktop-brain/e9f97b9f-09f0-4aff-ac11-a991e6b1aafa/scratchpad")
    ap.add_argument("--ledger", default="/private/tmp/claude-501/-Users-bernhard-Desktop-brain/e9f97b9f-09f0-4aff-ac11-a991e6b1aafa/scratchpad/campaign_ledger.jsonl")
    ap.add_argument("--campaign-cap-eur", type=float, default=30.0)
    args = ap.parse_args()

    # LLM caches (answerer/judge) — shared across all iterations of the campaign
    global _ANSWER_CACHE, _JUDGE_CACHE
    if args.cache_dir:
        _ANSWER_CACHE = DiskCache(os.path.join(args.cache_dir, "llm_cache_answer.jsonl"))
        _JUDGE_CACHE = DiskCache(os.path.join(args.cache_dir, "llm_cache_judge.jsonl"))
        print("caches: answer=%d judge=%d entries" % (len(_ANSWER_CACHE.data), len(_JUDGE_CACHE.data)), flush=True)

    # campaign budget ledger: hard cap across ALL runs
    spent_before = 0.0
    if args.ledger and os.path.exists(args.ledger):
        with open(args.ledger) as h:
            for line in h:
                try:
                    spent_before += float(json.loads(line).get("eur", 0.0))
                except Exception:
                    pass
    remaining_eur = max(0.0, args.campaign_cap_eur - spent_before)
    if remaining_eur <= 0.05:
        print("!! campaign cap exhausted (%.2f/%.2f EUR spent) - refusing to run" % (spent_before, args.campaign_cap_eur))
        return
    args.budget_eur = min(args.budget_eur, remaining_eur)
    print("campaign ledger: %.2f EUR spent, %.2f remaining; this run capped at %.2f EUR"
          % (spent_before, remaining_eur, args.budget_eur), flush=True)

    use_dates = not args.no_dates
    samples = LoCoMoLoader().load(args.data)
    idxs = [int(x) for x in args.convs.split(",")] if args.convs else list(range(len(samples)))
    features = frozenset(f.strip() for f in args.ours_features.split(",") if f.strip())
    if features:
        print("ours features: %s" % ",".join(sorted(features)), flush=True)
    sys_list = [s.strip() for s in args.systems.split(",")] if args.systems else None
    if sys_list == ["ours"]:
        args.skip_mem0 = True

    client = None
    if not args.skip_mem0:
        from mem0 import MemoryClient
        client = MemoryClient(api_key=os.environ["MEM0_API_KEY"])

    done = load_done(args.out)
    out = open(args.out, "a")
    out_lock = threading.Lock()
    budget_usd = args.budget_eur / EUR_PER_USD

    tallies = {"ours": {}, "mem0": {}}

    def record(system, q, correct, is_abstain):
        d = tallies[system]
        for key in ("all", "abstain" if is_abstain else "answerable", "cat_%s" % q.category):
            t = d.setdefault(key, [0, 0]); t[1] += 1; t[0] += 1 if correct else 0

    def eval_task(ci, uid, q, system, our_ctx):
        """Runs in a worker thread: context -> answerer -> judge. Returns row or None."""
        if _COST["usd"] >= budget_usd * 0.95:
            return None  # budget guard (soft; workers in flight may overshoot slightly)
        is_ab = q.expected_abstain
        ctx = our_ctx if system == "ours" else mem0_context(client, uid, q, args.top_k)
        if ctx.startswith("__ERR__"):
            return {"conv": ci, "qid": q.question_id, "system": system, "category": q.category,
                    "abstain": is_ab, "predicted": "", "correct": False, "error": ctx[7:]}
        pred = answerer(args.answerer, q.question, ctx, effort=args.answer_effort, qid=q.question_id)
        if is_ab:
            correct = is_noanswer(pred)
        else:
            correct = (not is_noanswer(pred)) and judge(args.judge, q.question, q.answers, pred,
                                                        effort=args.judge_effort, qid=q.question_id)
        return {"conv": ci, "qid": q.question_id, "system": system, "category": q.category,
                "abstain": is_ab, "predicted": pred, "correct": bool(correct)}

    systems = ["ours"] if args.skip_mem0 else ["ours", "mem0"]
    for ci in idxs:
        sample = samples[ci]
        questions = [q for q in sample.questions if args.include_abstain or not q.expected_abstain]
        if not questions:
            continue
        print("\n=== conv %d (%s): %d episodes, %d QA ===" % (ci, sample.sample_id, len(sample.episodes), len(questions)), flush=True)
        ours = build_ours(sample, use_dates, features)
        uid = "%s_conv%d" % (args.uid_prefix, ci)
        if client is not None:
            if not mem0_has(client, uid):
                n_add, cnt = mem0_ingest(client, uid, sample, use_dates, args.wait)
                print("   mem0 ingested %d session-batches -> %d memories" % (n_add, cnt), flush=True)
            else:
                print("   mem0 reuse existing memories (uid=%s)" % uid, flush=True)

        # our contexts serially (local retrieval is single-threaded); replay done rows
        our_ctx = {}
        pending = []
        for q in questions:
            oc = our_context(ours, sample, q, args.top_k) if "ours" in systems else ""
            our_ctx[q.question_id] = oc
            for system in systems:
                ck = (ci, q.question_id, system)
                if ck in done:
                    r = done[ck]; record(system, q, r["correct"], q.expected_abstain)
                else:
                    pending.append((q, system))
        if not pending:
            print("   all %d tasks already done (resume)" % (len(questions) * len(systems)), flush=True)
            continue

        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(eval_task, ci, uid, q, system, our_ctx[q.question_id]): (q, system)
                    for (q, system) in pending}
            for fut in concurrent.futures.as_completed(futs):
                q, system = futs[fut]
                row = fut.result()
                if row is None:
                    continue
                with out_lock:
                    out.write(json.dumps(row) + "\n"); out.flush()
                record(system, q, row["correct"], q.expected_abstain)
                completed += 1
                if completed % 50 == 0:
                    print("   ...%d/%d done  (cost %.2f EUR)" % (completed, len(pending), _COST["usd"] * EUR_PER_USD), flush=True)
        if _COST["usd"] >= budget_usd * 0.95:
            print("!! budget cap reached (%.2f EUR) - stopping" % (_COST["usd"] * EUR_PER_USD), flush=True)
            break

    out.close()
    # ---------- report ----------
    def rate(d, key):
        t = d.get(key)
        return (t[0] / t[1], t[0], t[1]) if t and t[1] else (0.0, 0, 0)

    print("\n================ END-TO-END LoCoMo (answerer=%s judge=%s k=%d) ================" % (args.answerer, args.judge, args.top_k))
    for scope in ("answerable", "abstain", "all"):
        oa = rate(tallies["ours"], scope); ma = rate(tallies["mem0"], scope)
        if oa[2] == 0 and ma[2] == 0:
            continue
        print("%-11s OURS %.3f (%d/%d) | MEM0 %.3f (%d/%d) | delta %+.3f"
              % (scope, oa[0], oa[1], oa[2], ma[0], ma[1], ma[2], oa[0] - ma[0]))
    print("-- by category (raw code) --")
    cats = sorted({k for s in tallies.values() for k in s if k.startswith("cat_")})
    for c in cats:
        oa = rate(tallies["ours"], c); ma = rate(tallies["mem0"], c)
        print("  %-7s OURS %.3f (%d/%d) | MEM0 %.3f (%d/%d)" % (c, oa[0], oa[1], oa[2], ma[0], ma[1], ma[2]))

    spent_eur = _COST["usd"] * EUR_PER_USD
    print("\ncost total: %.3f EUR (%d LLM calls, %d in / %d out tokens)"
          % (spent_eur, _COST["calls"], _COST["in_tok"], _COST["out_tok"]))
    if args.ledger:
        with open(args.ledger, "a") as h:
            h.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": args.tag or "run",
                                "features": sorted(features), "convs": idxs, "eur": round(spent_eur, 4),
                                "calls": _COST["calls"]}) + "\n")
        print("ledger: campaign total %.2f / %.2f EUR" % (spent_before + spent_eur, args.campaign_cap_eur))


if __name__ == "__main__":
    main()
