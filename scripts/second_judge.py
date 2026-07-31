"""Cross-vendor second judge: re-score stored predictions with a Claude judge.

Purpose: the primary LoCoMo E2E comparison was judged by gpt-5 — the same vendor
as the answerer. This script re-judges the STORED per-QA predictions of BOTH
systems (no retrieval, no Mem0 access) with an Anthropic judge using the
IDENTICAL judge instruction, so both the original baseline finding (Mem0 > our
baseline) and the optimized finding (ours > Mem0) can be confirmed or refuted by
an independent judge vendor.

Scoring rules mirror the primary harness exactly:
- answerable + non-NO-ANSWER prediction -> judge call (semantic correctness)
- answerable + NO-ANSWER               -> wrong (no judge needed)
- adversarial/no-info question         -> correct iff NO-ANSWER (deterministic)

Cost control: disk cache keyed (set, qid, judge model, normalized prediction);
a --pilot N mode measures real token usage and extrapolates before committing.
No model fallback: if the account runs out of credit mid-run, the script stops,
reports how much is missing, and a rerun resumes from cache without re-paying.
ANTHROPIC_API_KEY comes from the environment and is never written to any file.
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, "src")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


e2e = _load("e2e", os.path.join(os.path.dirname(__file__), "mem0_locomo_e2e.py"))
from cognitive_memory.locomo_eval import LoCoMoLoader
from cognitive_memory.stats import mcnemar_from_pairs, wilson_point_and_interval

USAGE = {"in": 0, "out": 0, "calls": 0, "credit_dead": False}
_LOCK = threading.Lock()


def claude_judge(model, question, golds, predicted, retries=6):
    key = os.environ["ANTHROPIC_API_KEY"]
    gold = " | ".join(str(g) for g in golds)
    body = json.dumps({
        "model": model, "max_tokens": 8,  # temperature is deprecated on claude-opus-5
        "system": e2e.JUDGE_SYS,
        "messages": [{"role": "user", "content": "Question: %s\nGold answer(s): %s\nPredicted: %s\nCorrect?"
                      % (question, gold, predicted)}],
    }).encode("utf-8")
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = "".join(b.get("text", "") for b in payload.get("content", []))
            usage = payload.get("usage", {})
            with _LOCK:
                USAGE["in"] += int(usage.get("input_tokens", 0))
                USAGE["out"] += int(usage.get("output_tokens", 0))
                USAGE["calls"] += 1
            return text.strip().upper().startswith("Y")
        except urllib.error.HTTPError as err:
            last = "%s %s" % (err.code, err.read().decode("utf-8", "ignore")[:200])
            if "credit" in last.lower() or "billing" in last.lower():
                USAGE["credit_dead"] = True
                raise RuntimeError("ANTHROPIC_CREDIT_EXHAUSTED: %s" % last)
            if err.code in (429, 500, 502, 503, 529):
                time.sleep(min(2 ** attempt, 30)); continue
            raise RuntimeError("anthropic %s" % last)
        except (urllib.error.URLError, TimeoutError) as err:
            last = str(err); time.sleep(min(2 ** attempt, 30))
    raise RuntimeError("anthropic failed after retries: %s" % last)


def load_predictions(path, system):
    rows = {}
    for line in open(path):
        r = json.loads(line)
        if r["system"] == system:
            rows[(r["conv"], r["qid"])] = r
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--baseline", default="docs/runs/locomo_e2e_ours_vs_mem0.jsonl")
    ap.add_argument("--optimized", default="docs/runs/locomo_e2e_ours_optimized.jsonl")
    ap.add_argument("--pilot", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--cache", default="/private/tmp/claude-501/-Users-bernhard-Desktop-brain/e9f97b9f-09f0-4aff-ac11-a991e6b1aafa/scratchpad/judge2_cache.jsonl")
    ap.add_argument("--out", default="/private/tmp/claude-501/-Users-bernhard-Desktop-brain/e9f97b9f-09f0-4aff-ac11-a991e6b1aafa/scratchpad/judge2_results.json")
    args = ap.parse_args()

    cache = e2e.DiskCache(args.cache)
    samples = LoCoMoLoader().load("data/external/locomo/locomo10.json")
    qinfo = {}
    for ci, s in enumerate(samples):
        for q in s.questions:
            qinfo[(ci, q.question_id)] = q

    sets = {
        "ours_base": load_predictions(args.baseline, "ours"),
        "mem0": load_predictions(args.baseline, "mem0"),
        "ours_opt": load_predictions(args.optimized, "ours"),
    }

    # build the task list: answerable + non-noanswer need a judge call
    tasks = []
    for set_name, rows in sets.items():
        for (ci, qid), r in rows.items():
            q = qinfo.get((ci, qid))
            if q is None or q.expected_abstain:
                continue
            if e2e.is_noanswer(r.get("predicted", "")):
                continue  # deterministically wrong on answerable
            ck = e2e._h("j2", set_name, qid, args.model, (r["predicted"] or "").strip().lower())
            if cache.get(ck) is None:
                tasks.append((set_name, ci, qid, q, r["predicted"], ck))
    total_needed = len(tasks)
    if args.pilot:
        tasks = tasks[: args.pilot]
    print("judge2 model=%s | judge calls needed=%d (running %d)" % (args.model, total_needed, len(tasks)), flush=True)

    stop = threading.Event()

    def work(task):
        if stop.is_set():
            return None
        set_name, ci, qid, q, pred, ck = task
        try:
            verdict = claude_judge(args.model, q.question, q.answers, pred)
        except RuntimeError as err:
            if "CREDIT_EXHAUSTED" in str(err):
                stop.set()
            raise
        cache.put(ck, bool(verdict))
        return True

    done_n = 0
    err_msg = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(work, t) for t in tasks]
        for fut in concurrent.futures.as_completed(futs):
            try:
                if fut.result() is not None:
                    done_n += 1
                    if done_n % 200 == 0:
                        print("  ...%d/%d judged (tok in=%d out=%d)" % (done_n, len(tasks), USAGE["in"], USAGE["out"]), flush=True)
            except RuntimeError as err:
                err_msg = str(err)[:200]
    print("judged %d new; token usage: %d in / %d out (%d calls)" % (done_n, USAGE["in"], USAGE["out"], USAGE["calls"]), flush=True)
    for pin, pout, tag in ((5.0, 25.0, "$5/$25"), (15.0, 75.0, "$15/$75")):
        usd = (USAGE["in"] * pin + USAGE["out"] * pout) / 1e6
        print("  cost if %s per MTok: $%.2f (EUR %.2f)" % (tag, usd, usd * 0.92))
    if args.pilot and done_n:
        remaining = total_needed - done_n
        per_in = USAGE["in"] / max(done_n, 1); per_out = USAGE["out"] / max(done_n, 1)
        for pin, pout, tag in ((5.0, 25.0, "$5/$25"), (15.0, 75.0, "$15/$75")):
            usd = remaining * (per_in * pin + per_out * pout) / 1e6
            print("PILOT extrapolation, remaining %d calls at %s: ~$%.2f (EUR %.2f)" % (remaining, tag, usd, usd * 0.92))
        return
    if err_msg and "CREDIT" in err_msg:
        remaining = total_needed - done_n
        per_in = (USAGE["in"] / max(done_n, 1)) if done_n else 250
        est = remaining * (per_in * 15.0) / 1e6
        print("!! ANTHROPIC CREDIT EXHAUSTED after %d/%d. Remaining ~%d calls; top up roughly $%.0f-%.0f and rerun (cache resumes)."
              % (done_n, total_needed, remaining, est * 0.4, est * 1.2))
        return

    # ---------- score all three sets under judge2 (cache-complete now) ----------
    verdicts = {}
    for set_name, rows in sets.items():
        per = {}
        for (ci, qid), r in rows.items():
            q = qinfo.get((ci, qid))
            if q is None:
                continue
            if q.expected_abstain:
                correct = e2e.is_noanswer(r.get("predicted", ""))
                per[(ci, qid)] = ("abstain", bool(correct))
            elif e2e.is_noanswer(r.get("predicted", "")):
                per[(ci, qid)] = ("answerable", False)
            else:
                ck = e2e._h("j2", set_name, qid, args.model, (r["predicted"] or "").strip().lower())
                v = cache.get(ck)
                if v is None:
                    continue
                per[(ci, qid)] = ("answerable", bool(v))
        verdicts[set_name] = per

    def acc(set_name, kind):
        per = verdicts[set_name]
        hits = [ok for (k, ok) in per.values() if k == kind]
        n = len(hits)
        return (sum(hits), n)

    def paired(a_set, b_set):
        pa, pb = verdicts[a_set], verdicts[b_set]
        keys = [k for k in pa if k in pb and pa[k][0] == "answerable" and pb[k][0] == "answerable"]
        av = [pa[k][1] for k in keys]; bv = [pb[k][1] for k in keys]
        b, c, nd, p = mcnemar_from_pairs(av, bv)
        return (sum(av), sum(bv), len(keys), b, c, p)

    report = {"model": args.model, "usage": dict(USAGE)}
    print("\n================ SECOND JUDGE (%s) ================" % args.model)
    for set_name, label in (("ours_base", "OURS baseline"), ("mem0", "MEM0"), ("ours_opt", "OURS optimized")):
        c_ans, n_ans = acc(set_name, "answerable")
        c_ab, n_ab = acc(set_name, "abstain")
        p, lo, hi = wilson_point_and_interval(c_ans, max(n_ans, 1))
        print("%-14s answerable %.3f [%.3f,%.3f] (%d/%d)   abstain %.3f (%d/%d)"
              % (label, p, lo, hi, c_ans, n_ans, (c_ab / n_ab if n_ab else 0), c_ab, n_ab))
        report[set_name] = {"answerable": [c_ans, n_ans], "abstain": [c_ab, n_ab]}
    for a, b, label in (("ours_base", "mem0", "BASELINE ours vs mem0"), ("ours_opt", "mem0", "OPTIMIZED ours vs mem0")):
        ca, cb, n, x, y, pval = paired(a, b)
        if n == 0:
            continue
        print("%-24s: %.3f vs %.3f (n=%d)  diff %+0.3f  McNemar a-only=%d b-only=%d p=%.3g"
              % (label, ca / n, cb / n, n, (ca - cb) / n, x, y, pval))
        report[label] = {"a": ca, "b": cb, "n": n, "p": pval}

    # judge agreement (Cohen's kappa) vs the primary gpt-5 verdicts on shared judged items
    def kappa(set_name, rows):
        per = verdicts[set_name]
        pairs = []
        for (ci, qid), r in rows.items():
            item = per.get((ci, qid))
            if item is None or item[0] != "answerable" or e2e.is_noanswer(r.get("predicted", "")):
                continue
            pairs.append((bool(r["correct"]), item[1]))
        if not pairs:
            return None, 0
        n = len(pairs)
        po = sum(1 for a, b in pairs if a == b) / n
        pa1 = sum(1 for a, _ in pairs if a) / n; pb1 = sum(1 for _, b in pairs if b) / n
        pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
        return (po - pe) / (1 - pe) if pe < 1 else 1.0, n

    print("\n-- judge agreement vs gpt-5 (answerable, judged items) --")
    for set_name, rows in sets.items():
        k, n = kappa(set_name, rows)
        if k is not None:
            print("  %-10s Cohen's kappa = %.3f (n=%d)" % (set_name, k, n))
            report.setdefault("kappa", {})[set_name] = [k, n]

    json.dump(report, open(args.out, "w"), indent=1)
    print("\nwrote", args.out)


if __name__ == "__main__":
    main()
