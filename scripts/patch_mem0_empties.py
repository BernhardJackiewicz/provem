"""Fairness patch: re-ask Mem0's EMPTY stored predictions with the fixed harness.

A harness bug let reasoning models burn the whole completion budget and return
empty content with finish_reason=length (HTTP 200) — silently scored as wrong on
answerable questions. It hit BOTH systems (ours optimized: 352/1540 answerable,
Mem0: 195/1540). Our side can be re-run locally; Mem0's side needs its stores,
which still exist under the original account. This script re-asks ONLY Mem0's
empty predictions (answerable AND adversarial, symmetric rule) through the same
pipeline (store search top-k=20, neutral answerer prompt pv=v1, fixed token
handling) and writes a patched copy of the frozen mem0 rows.

Documented caveat: Mem0's stores kept distilling after the original freeze
(store drift). Drift adds/refines memories, which favors Mem0 — the patch is
therefore conservative with respect to our comparison.
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="docs/runs/locomo_e2e_ours_vs_mem0.jsonl")
    ap.add_argument("--out", default="docs/runs/locomo_e2e_mem0_patched.jsonl")
    ap.add_argument("--uid-prefix", default="engram_e2e")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cache-dir", default="docs/runs/caches")
    args = ap.parse_args()

    e2e._ANSWER_CACHE = e2e.DiskCache(os.path.join(args.cache_dir, "llm_cache_answer.jsonl"))
    e2e._JUDGE_CACHE = e2e.DiskCache(os.path.join(args.cache_dir, "llm_cache_judge.jsonl"))

    from mem0 import MemoryClient
    client = MemoryClient(api_key=os.environ["MEM0_API_KEY"])

    samples = LoCoMoLoader().load("data/external/locomo/locomo10.json")
    qinfo = {}
    for ci, s in enumerate(samples):
        for q in s.questions:
            qinfo[(ci, q.question_id)] = q

    rows = []
    empties = []
    for line in open(args.baseline):
        r = json.loads(line)
        if r["system"] != "mem0":
            continue
        rows.append(r)
        if not (r.get("predicted") or "").strip():
            empties.append(r)
    print("mem0 rows: %d | empty predictions to re-ask: %d (answerable %d, abstain %d)"
          % (len(rows), len(empties),
             sum(1 for r in empties if not r["abstain"]),
             sum(1 for r in empties if r["abstain"])), flush=True)

    lock = threading.Lock()
    fixed = {"n": 0, "now_nonempty": 0, "now_correct_answerable": 0}

    def work(r):
        q = qinfo[(r["conv"], r["qid"])]
        uid = "%s_conv%d" % (args.uid_prefix, r["conv"])
        ctx = e2e.mem0_context(client, uid, q, args.top_k)
        if ctx.startswith("__ERR__"):
            return r, None
        pred = e2e.answerer("gpt-5-mini", q.question, ctx, effort="medium", qid=q.question_id)
        if r["abstain"]:
            correct = e2e.is_noanswer(pred)
        else:
            correct = (not e2e.is_noanswer(pred)) and e2e.judge("gpt-5", q.question, q.answers, pred,
                                                               effort="low", qid=q.question_id)
        with lock:
            fixed["n"] += 1
            if pred.strip():
                fixed["now_nonempty"] += 1
            if (not r["abstain"]) and correct:
                fixed["now_correct_answerable"] += 1
            if fixed["n"] % 40 == 0:
                print("  ...%d/%d re-asked" % (fixed["n"], len(empties)), flush=True)
        patched = dict(r)
        patched["predicted"] = pred
        patched["correct"] = bool(correct)
        patched["patched"] = "empty_fix"
        return r, patched

    patched_by_key = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for r, patched in pool.map(work, empties):
            if patched is not None:
                patched_by_key[(r["conv"], r["qid"])] = patched

    with open(args.out, "w") as h:
        for r in rows:
            h.write(json.dumps(patched_by_key.get((r["conv"], r["qid"]), r)) + "\n")
    print("patched %d rows (%d now non-empty, %d answerable now correct) -> %s"
          % (len(patched_by_key), fixed["now_nonempty"], fixed["now_correct_answerable"], args.out))
    print("openai cost: EUR %.2f" % (e2e._COST["usd"] * e2e.EUR_PER_USD))


if __name__ == "__main__":
    main()
