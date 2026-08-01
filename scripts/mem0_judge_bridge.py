"""Bridge test: score BOTH systems' stored predictions with Mem0's OFFICIAL judge.

Purpose: our strict binary judge scores Mem0 at ~0.51; Mem0's own published runs
report 82-92%. Before publishing any comparison we verify the gap is a JUDGE
artifact, not a harness error: we re-score our stored predictions (both systems,
post empty-fix) with Mem0's own judge prompt, verbatim from their public
benchmark repo (mem0ai/memory-benchmarks benchmarks/locomo/prompts.py — partial
credit, paraphrase equivalence, 14-day date tolerance, no-evidence variant) and
their 2026 judge model class (gpt-5). Congruence targets: independent ENGRAM
paper measured Mem0 at 64.7 (k=20, lenient judge); Mem0's own top_50 run is 82.66
(k=50, gpt-5 CoT answerer). Our k=20/gpt-5-mini setup should land Mem0 between
those under this judge. Same scoring applied to OUR predictions for the paired
comparison under THEIR rules. Keys from env only.
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import sys
import threading

sys.path.insert(0, "src")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


e2e = _load("e2e", os.path.join(os.path.dirname(__file__), "mem0_locomo_e2e.py"))
from cognitive_memory.locomo_eval import LoCoMoLoader
from cognitive_memory.stats import mcnemar_from_pairs, wilson_point_and_interval

# Mem0's official judge template (no-evidence variant), fetched verbatim from
# github.com/mem0ai/memory-benchmarks benchmarks/locomo/prompts.py; the
# {question}/{answer}/{response} placeholders are theirs.
MEM0_JUDGE_SYSTEM = "You are evaluating conversational AI memory recall. Return JSON only with the format requested."
_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "prompts", "mem0_judge_template.txt")
_template_text = None


def _get_template():
    global _template_text
    if _template_text is None:
        if not os.path.exists(_TEMPLATE_PATH):
            raise FileNotFoundError(
                "Mem0 judge template not found at %s (tracked in the repo under "
                "scripts/prompts/; restore it before judging)" % _TEMPLATE_PATH)
        with open(_TEMPLATE_PATH) as fh:
            _template_text = fh.read()
    return _template_text


def mem0_judge(model, question, golds, predicted, qid, cache):
    key = e2e._h("m0j", qid, model, (predicted or "").strip().lower())
    cached = cache.get(key)
    if cached is not None:
        return bool(cached)
    gold = "; ".join(str(g) for g in golds)
    user = _get_template().replace("{question}", question).replace("{answer}", gold).replace("{response}", predicted)
    msgs = [{"role": "system", "content": MEM0_JUDGE_SYSTEM}, {"role": "user", "content": user}]
    text, _ = e2e.openai_chat(model, msgs, max_tokens=256, reasoning_effort="low")
    m = re.search(r'"label"\s*:\s*"(CORRECT|WRONG)"', text, re.I)
    verdict = bool(m and m.group(1).upper() == "CORRECT")
    cache.put(key, verdict)
    return verdict


def load_predictions(path, system):
    rows = {}
    for line in open(path):
        r = json.loads(line)
        if r["system"] == system:
            rows[(r["conv"], r["qid"])] = r
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge-model", default="gpt-5")
    ap.add_argument("--ours", default="docs/runs/iter/c2_full_final2.jsonl")
    ap.add_argument("--mem0", default="docs/runs/locomo_e2e_mem0_patched.jsonl")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--cache", default="docs/runs/caches/mem0_judge_cache.jsonl")
    args = ap.parse_args()

    cache = e2e.DiskCache(args.cache)
    samples = LoCoMoLoader().load("data/external/locomo/locomo10.json")
    qinfo = {}
    for ci, s in enumerate(samples):
        for q in s.questions:
            qinfo[(ci, q.question_id)] = q

    sets = {"ours": load_predictions(args.ours, "ours"), "mem0": load_predictions(args.mem0, "mem0")}
    # Mem0 convention: categories 1-4 only (answerable, n=1540); empty answers judged too
    # (their pipeline always produces text; our NO-ANSWER predictions are judged as-is —
    # the judge will mark them WRONG, matching their protocol's treatment of misses).
    tasks = []
    for set_name, rows in sets.items():
        for (ci, qid), r in rows.items():
            q = qinfo.get((ci, qid))
            if q is None or q.expected_abstain:
                continue
            tasks.append((set_name, ci, qid, q, r.get("predicted", "")))
    todo = [t for t in tasks if cache.get(e2e._h("m0j", t[2], args.judge_model, (t[4] or "").strip().lower())) is None]
    print("mem0-judge bridge: %d scored items, %d need calls (judge=%s)" % (len(tasks), len(todo), args.judge_model), flush=True)

    done = [0]
    lock = threading.Lock()

    def work(t):
        set_name, ci, qid, q, pred = t
        mem0_judge(args.judge_model, q.question, q.answers, pred, qid, cache)
        with lock:
            done[0] += 1
            if done[0] % 200 == 0:
                print("  ...%d/%d (EUR %.2f)" % (done[0], len(todo), e2e._COST["usd"] * e2e.EUR_PER_USD), flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, todo))

    # score
    verdicts = {}
    for set_name, rows in sets.items():
        per = {}
        for (ci, qid), r in rows.items():
            q = qinfo.get((ci, qid))
            if q is None or q.expected_abstain:
                continue
            key = e2e._h("m0j", qid, args.judge_model, (r.get("predicted", "") or "").strip().lower())
            v = cache.get(key)
            if v is not None:
                per[(ci, qid)] = bool(v)
        verdicts[set_name] = per

    print("\n========== UNDER MEM0'S OWN JUDGE (%s, their official prompt) ==========" % args.judge_model)
    for set_name in ("ours", "mem0"):
        per = verdicts[set_name]
        c, n = sum(per.values()), len(per)
        p, lo, hi = wilson_point_and_interval(c, max(n, 1))
        print("%-5s answerable: %.3f [%.3f,%.3f] (%d/%d)" % (set_name.upper(), p, lo, hi, c, n))
    keys = [k for k in verdicts["ours"] if k in verdicts["mem0"]]
    a = [verdicts["ours"][k] for k in keys]
    b = [verdicts["mem0"][k] for k in keys]
    x, y, nd, pval = mcnemar_from_pairs(a, b)
    print("PAIRED: ours %.3f vs mem0 %.3f (n=%d)  diff %+.3f  McNemar ours-only=%d mem0-only=%d p=%.3g"
          % (sum(a) / len(a), sum(b) / len(b), len(keys), (sum(a) - sum(b)) / len(keys), x, y, pval))
    print("cost: EUR %.2f" % (e2e._COST["usd"] * e2e.EUR_PER_USD))


if __name__ == "__main__":
    main()
