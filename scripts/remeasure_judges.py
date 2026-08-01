"""Judge top-up for the corrected re-measurement (v2) arms.

Runs the claude-opus-5 cross-vendor judge and Mem0's own published judge over
the freshly measured Mem0/Zep v2 prediction files, into the same frozen caches
the v1 scoreboard uses. The Provem arm is frozen and fully cached (EUR 0).

Cache-key scheme (preregistered in docs/remeasurement_protocol.md):
  opus: _h("j2", <set_name>, qid, "claude-opus-5", pred_norm)
        set_name "mem0_v2" / "zep_v2" — distinct from the historical sets.
  m0j : _h("m0j", qid, "gpt-5", pred_norm) — deliberately system-agnostic;
        identical predictions hit legitimately (the judge sees only
        question+gold+prediction).

Always prints needed/cached/to-run counts per judge per arm BEFORE spending.
Use --pilot N for a cost extrapolation on N calls per judge.
"""
import argparse
import concurrent.futures
import importlib.util
import os
import sys
import threading

sys.path.insert(0, "src")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HERE = os.path.dirname(__file__)
e2e = _load("e2e", os.path.join(HERE, "mem0_locomo_e2e.py"))
sj = _load("sj", os.path.join(HERE, "second_judge.py"))
br = _load("br", os.path.join(HERE, "mem0_judge_bridge.py"))
from cognitive_memory.locomo_eval import LoCoMoLoader


def load_rows(path, system):
    import json
    rows = {}
    for line in open(path):
        r = json.loads(line)
        if r["system"] == system:
            rows[(r["conv"], r["qid"])] = r
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mem0", default="docs/runs/locomo_e2e_mem0_v2.jsonl")
    ap.add_argument("--zep", default="docs/runs/zep_locomo_v2.jsonl")
    ap.add_argument("--cache-dir", default="docs/runs/caches")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--pilot", type=int, default=0, help="run only N calls per judge, extrapolate cost, exit")
    ap.add_argument("--skip-opus", action="store_true")
    ap.add_argument("--skip-m0j", action="store_true")
    args = ap.parse_args()

    opus_cache = e2e.DiskCache(os.path.join(args.cache_dir, "judge2_cache.jsonl"))
    m0j_cache = e2e.DiskCache(os.path.join(args.cache_dir, "mem0_judge_cache.jsonl"))

    samples = LoCoMoLoader().load("data/external/locomo/locomo10.json")
    qinfo = {}
    for ci, s in enumerate(samples):
        for q in s.questions:
            qinfo[(ci, q.question_id)] = q

    arms = {"mem0_v2": load_rows(args.mem0, "mem0"), "zep_v2": load_rows(args.zep, "zep")}

    tasks = []
    for set_name, rows in arms.items():
        stats = {"opus": [0, 0], "m0j": [0, 0]}  # [needed, cached]
        for (ci, qid), r in rows.items():
            q = qinfo.get((ci, qid))
            if q is None or q.expected_abstain:
                continue
            pred = r.get("predicted", "")
            pn = (pred or "").strip().lower()
            if not args.skip_opus and not e2e.is_noanswer(pred):
                stats["opus"][0] += 1
                key = e2e._h("j2", set_name, qid, "claude-opus-5", pn)
                if opus_cache.get(key) is None:
                    tasks.append(("opus", set_name, ci, qid, q, pred, key))
                else:
                    stats["opus"][1] += 1
            if not args.skip_m0j:
                # bridge protocol: ALL answerable predictions judged, incl. NO-ANSWER
                stats["m0j"][0] += 1
                key = e2e._h("m0j", qid, "gpt-5", pn)
                if m0j_cache.get(key) is None:
                    tasks.append(("m0j", set_name, ci, qid, q, pred, key))
                else:
                    stats["m0j"][1] += 1
        for judge in ("opus", "m0j"):
            need, cached = stats[judge]
            print("%s / %s: needed=%d cached=%d to-run=%d" % (set_name, judge, need, cached, need - cached), flush=True)

    if args.pilot:
        by = {}
        for t in tasks:
            by.setdefault(t[0], []).append(t)
        tasks = [t for judge, ts in by.items() for t in ts[: args.pilot]]
        print("PILOT: running %d calls total" % len(tasks), flush=True)

    lock = threading.Lock()
    done = [0]

    def work(t):
        kind, set_name, ci, qid, q, pred, key = t
        if kind == "opus":
            v = sj.claude_judge("claude-opus-5", q.question, q.answers, pred)
            opus_cache.put(key, bool(v))
        else:
            br.mem0_judge("gpt-5", q.question, q.answers, pred, qid, m0j_cache)
        with lock:
            done[0] += 1
            if done[0] % 100 == 0:
                print("  ...%d/%d (openai EUR %.2f)" % (done[0], len(tasks), e2e._COST["usd"] * e2e.EUR_PER_USD), flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, tasks))

    print("ran %d judge calls | openai cost EUR %.2f (opus billed on the Anthropic side)"
          % (len(tasks), e2e._COST["usd"] * e2e.EUR_PER_USD), flush=True)
    if args.pilot and tasks:
        per_call = (e2e._COST["usd"] * e2e.EUR_PER_USD) / max(1, sum(1 for t in tasks if t[0] == "m0j"))
        print("PILOT extrapolation (m0j only, openai side): EUR %.4f/call" % per_call, flush=True)


if __name__ == "__main__":
    main()
