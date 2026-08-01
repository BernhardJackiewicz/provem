"""Second and third judge over the Zep arm's stored predictions.

Applies the identical scoring the other two systems received:
  - claude-opus-5 cross-vendor judge (same JUDGE_SYS wording as gpt-5 primary)
  - Mem0's own published judge prompt (gpt-5)
Both cached; abstention stays deterministic (is_noanswer). Prints the paired
three-system view at the end (Provem final2 vs Mem0 patched vs Zep) under each
judge. Keys from env only.
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


HERE = os.path.dirname(__file__)
e2e = _load("e2e", os.path.join(HERE, "mem0_locomo_e2e.py"))
sj = _load("sj", os.path.join(HERE, "second_judge.py"))
br = _load("br", os.path.join(HERE, "mem0_judge_bridge.py"))
from cognitive_memory.locomo_eval import LoCoMoLoader
from cognitive_memory.stats import wilson_point_and_interval


def load_rows(path, system):
    rows = {}
    for line in open(path):
        r = json.loads(line)
        if r["system"] == system:
            rows[(r["conv"], r["qid"])] = r
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zep", default="docs/runs/zep_locomo.jsonl")
    ap.add_argument("--ours", default="docs/runs/iter/c2_full_final2.jsonl")
    ap.add_argument("--mem0", default="docs/runs/locomo_e2e_mem0_patched.jsonl")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--skip-opus", action="store_true")
    ap.add_argument("--skip-mem0-judge", action="store_true")
    ap.add_argument("--cache-dir", default="docs/runs/caches")
    ap.add_argument("--top-up", action="store_true",
                    help="allow paid judge calls for missing verdicts; without it a replay never spends money")
    ap.add_argument("--allow-missing", action="store_true",
                    help="score missing verdicts as incorrect instead of aborting (intentional partial scoring only)")
    args = ap.parse_args()

    opus_cache = e2e.DiskCache(os.path.join(args.cache_dir, "judge2_cache.jsonl"))
    m0j_cache = e2e.DiskCache(os.path.join(args.cache_dir, "mem0_judge_cache.jsonl"))

    samples = LoCoMoLoader().load("data/external/locomo/locomo10.json")
    qinfo = {}
    for ci, s in enumerate(samples):
        for q in s.questions:
            qinfo[(ci, q.question_id)] = q

    systems = {"provem": load_rows(args.ours, "ours"),
               "mem0": load_rows(args.mem0, "mem0"),
               "zep": load_rows(args.zep, "zep")}

    # ---- collect the judge tasks still missing for the zep rows ----
    tasks = []
    for (ci, qid), r in systems["zep"].items():
        q = qinfo.get((ci, qid))
        if q is None or q.expected_abstain:
            continue
        pred = r.get("predicted", "")
        if not args.skip_opus and not e2e.is_noanswer(pred):
            ok = e2e._h("j2", "zep", qid, "claude-opus-5", (pred or "").strip().lower())
            if opus_cache.get(ok) is None:
                tasks.append(("opus", ci, qid, q, pred, ok))
        if not args.skip_mem0_judge:
            mk = e2e._h("m0j", qid, "gpt-5", (pred or "").strip().lower())
            if m0j_cache.get(mk) is None:
                tasks.append(("m0j", ci, qid, q, pred, mk))
    print("zep judge top-up: %d calls needed" % len(tasks), flush=True)
    if tasks and not args.top_up:
        raise SystemExit(
            "ABORT: %d judge verdicts are missing from the caches. This is a replay "
            "(no paid calls); rerun with --top-up to spend, or --skip-opus/--skip-mem0-judge." % len(tasks))

    lock = threading.Lock()
    done = [0]

    def work(t):
        kind, ci, qid, q, pred, key = t
        if kind == "opus":
            v = sj.claude_judge("claude-opus-5", q.question, q.answers, pred)
            opus_cache.put(key, bool(v))
        else:
            v = br.mem0_judge("gpt-5", q.question, q.answers, pred, qid, m0j_cache)
        with lock:
            done[0] += 1
            if done[0] % 200 == 0:
                print("  ...%d/%d" % (done[0], len(tasks)), flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, tasks))

    # ---- three-system scoreboard under each judge ----
    def score(system, rows, regime, missing):
        c = n = 0
        for (ci, qid), r in rows.items():
            q = qinfo.get((ci, qid))
            if q is None or q.expected_abstain:
                continue
            pred = r.get("predicted", "")
            n += 1
            if regime == "strict":
                ok = bool(r["correct"])
            elif regime == "opus":
                if e2e.is_noanswer(pred):
                    ok = False
                else:
                    set_name = {"provem": "ours_opt", "mem0": "mem0", "zep": "zep"}[system]
                    v = opus_cache.get(e2e._h("j2", set_name, qid, "claude-opus-5", (pred or "").strip().lower()))
                    if v is None:
                        missing.append((regime, system, ci, qid))
                    ok = bool(v)
            else:  # mem0 judge
                v = m0j_cache.get(e2e._h("m0j", qid, "gpt-5", (pred or "").strip().lower()))
                if v is None:
                    missing.append((regime, system, ci, qid))
                ok = bool(v)
            c += 1 if ok else 0
        return c, n

    # compute everything first: a missing cached verdict must abort loudly
    # instead of being silently scored as incorrect (bool(None) deflated scores)
    missing = []
    results = {}
    regimes = [("strict", "strict gpt-5 judge")]
    if not args.skip_opus:
        regimes.append(("opus", "claude-opus-5 judge"))
    if not args.skip_mem0_judge:
        regimes.append(("m0j", "Mem0's own judge prompt"))
    for regime, _label in regimes:
        for system in ("provem", "mem0", "zep"):
            results[(regime, system)] = score(system, systems[system], regime, missing)
    if missing and not args.allow_missing:
        by = {}
        for regime, system, _ci, _qid in missing:
            by[(regime, system)] = by.get((regime, system), 0) + 1
        detail = ", ".join("%s/%s: %d" % (r, s, k) for (r, s), k in sorted(by.items()))
        raise SystemExit(
            "ABORT: %d judge verdicts missing from the caches (%s). Scores would be "
            "silently deflated. Run with --top-up to judge them, or --allow-missing "
            "to score them as incorrect on purpose." % (len(missing), detail))

    print("\n============ THREE-SYSTEM SCOREBOARD (answerable, n=1540) ============")
    for regime, label in regimes:
        parts = []
        for system in ("provem", "mem0", "zep"):
            c, n = results[(regime, system)]
            p, lo, hi = wilson_point_and_interval(c, max(n, 1))
            parts.append("%s %.3f (%d/%d)" % (system, p, c, n))
        print("%-26s: %s" % (label, " | ".join(parts)))
    # abstention (judge-independent)
    parts = []
    for system in ("provem", "mem0", "zep"):
        c = n = 0
        for (ci, qid), r in systems[system].items():
            q = qinfo.get((ci, qid))
            if q is None or not q.expected_abstain:
                continue
            n += 1
            c += 1 if e2e.is_noanswer(r.get("predicted", "")) else 0
        parts.append("%s %.3f (%d/%d)" % (system, c / max(n, 1), c, n))
    print("%-26s: %s" % ("abstention (446 adversarial)", " | ".join(parts)))


if __name__ == "__main__":
    main()
