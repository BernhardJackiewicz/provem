"""Single reproducible stats pipeline for the three-system scoreboard.

Pure artifact/cache reader: makes ZERO paid calls (asserted at exit). Computes
every preregistered number for Provem vs Mem0 vs Zep from the prediction files
and the frozen judge caches, fail-loud on any missing verdict, and emits both
JSON and a markdown block so docs are generated instead of hand-edited.

Numbers computed (the preregistered list in docs/remeasurement_protocol.md):
  - answerable accuracy + Wilson 95% CI, 3 systems x 3 judges
  - abstention accuracy on the 446 adversarial questions per system
  - per-category (strict judge) with the OFFICIAL LoCoMo display names
  - holdout-conversation (1,3,5,7,9) strict accuracy per system
  - all pairwise exact McNemars per judge (answerable) + abstention McNemars
  - Cohen's kappa strict-vs-opus per system

Determinism check: run it twice; the outputs must be byte-identical.
"""
import argparse
import importlib.util
import json
import os
import sys

sys.path.insert(0, "src")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


e2e = _load("e2e", os.path.join(os.path.dirname(__file__), "mem0_locomo_e2e.py"))
from cognitive_memory.locomo_eval import LoCoMoLoader
from cognitive_memory.stats import mcnemar_from_pairs, wilson_point_and_interval

# Official LoCoMo mapping (rows store raw codes, so this is display-only; the
# pre-correction library map at locomo_eval.py:29 was fixed 2026-08)
CATEGORY_DISPLAY = {"1": "multi_hop", "2": "temporal", "3": "open_domain", "4": "single_hop", "5": "adversarial"}
HOLDOUT_CONVS = {1, 3, 5, 7, 9}
EXPECTED_TOTAL, EXPECTED_ANSWERABLE, EXPECTED_ADVERSARIAL = 1986, 1540, 446


def load_rows(path, system):
    rows = {}
    for line in open(path):
        r = json.loads(line)
        if r["system"] != system:
            continue
        key = (r["conv"], r["qid"])
        if key in rows:
            raise SystemExit("duplicate row %s in %s" % (key, path))
        rows[key] = r
    return rows


def cohen_kappa(pairs):
    """pairs: list of (a, b) bools."""
    n = len(pairs)
    if not n:
        return float("nan")
    both = sum(1 for a, b in pairs if a and b)
    neither = sum(1 for a, b in pairs if not a and not b)
    po = (both + neither) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe != 1 else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ours", default="docs/runs/iter/c2_full_final2.jsonl")
    ap.add_argument("--mem0", default="docs/runs/locomo_e2e_mem0_patched.jsonl")
    ap.add_argument("--zep", default="docs/runs/zep_locomo.jsonl")
    ap.add_argument("--cache-dir", default="docs/runs/caches")
    ap.add_argument("--opus-sets", default="provem=ours_opt,mem0=mem0,zep=zep",
                    help="system=opus_set_name mapping (v2: provem=ours_opt,mem0=mem0_v2,zep=zep_v2)")
    ap.add_argument("--out", default=None, help="write the JSON report here")
    args = ap.parse_args()

    opus_cache = e2e.DiskCache(os.path.join(args.cache_dir, "judge2_cache.jsonl"))
    m0j_cache = e2e.DiskCache(os.path.join(args.cache_dir, "mem0_judge_cache.jsonl"))
    opus_sets = dict(kv.split("=") for kv in args.opus_sets.split(","))

    samples = LoCoMoLoader().load("data/external/locomo/locomo10.json")
    qinfo = {}
    for ci, s in enumerate(samples):
        for q in s.questions:
            qinfo[(ci, q.question_id)] = q

    systems = {"provem": load_rows(args.ours, "ours"),
               "mem0": load_rows(args.mem0, "mem0"),
               "zep": load_rows(args.zep, "zep")}

    # ---- input validation: abort on anything partial ----
    for name, rows in systems.items():
        if len(rows) != EXPECTED_TOTAL:
            raise SystemExit("%s: %d rows, expected %d" % (name, len(rows), EXPECTED_TOTAL))
        errs = [k for k, r in rows.items() if r.get("error")]
        if errs:
            raise SystemExit("%s: %d error rows (first: %s)" % (name, len(errs), errs[0]))
        unknown = [k for k in rows if k not in qinfo]
        if unknown:
            raise SystemExit("%s: %d rows with unknown qids (first: %s)" % (name, len(unknown), unknown[0]))
        n_adv = sum(1 for k, r in rows.items() if qinfo[k].expected_abstain)
        if n_adv != EXPECTED_ADVERSARIAL:
            raise SystemExit("%s: %d adversarial rows, expected %d" % (name, n_adv, EXPECTED_ADVERSARIAL))

    # ---- verdict resolution, fail-loud ----
    missing = []

    def verdict(system, judge, key, r):
        q = qinfo[key]
        pred = r.get("predicted", "")
        pn = (pred or "").strip().lower()
        if judge == "strict":
            return bool(r["correct"])
        if judge == "opus":
            if e2e.is_noanswer(pred):
                return False
            v = opus_cache.get(e2e._h("j2", opus_sets[system], q.question_id, "claude-opus-5", pn))
            if v is None:
                missing.append((judge, system) + key)
            return bool(v)
        v = m0j_cache.get(e2e._h("m0j", q.question_id, "gpt-5", pn))
        if v is None:
            missing.append((judge, system) + key)
        return bool(v)

    judges = ("strict", "opus", "m0j")
    answerable_keys = sorted(k for k in systems["provem"] if not qinfo[k].expected_abstain)
    adversarial_keys = sorted(k for k in systems["provem"] if qinfo[k].expected_abstain)
    verdicts = {(s, j): {k: verdict(s, j, k, systems[s][k]) for k in answerable_keys}
                for s in systems for j in judges}
    if missing:
        by = {}
        for judge, system, _c, _q in (m[:2] + (m[2], m[3]) for m in missing):
            by[(judge, system)] = by.get((judge, system), 0) + 1
        raise SystemExit("ABORT: %d judge verdicts missing from the caches (%s) — top up first (scripts/remeasure_judges.py)"
                         % (len(missing), ", ".join("%s/%s: %d" % (j, s, n) for (j, s), n in sorted(by.items()))))

    report = {"inputs": {"ours": args.ours, "mem0": args.mem0, "zep": args.zep, "opus_sets": opus_sets}}

    # ---- scoreboard ----
    board = {}
    for j in judges:
        row = {}
        for s in systems:
            v = verdicts[(s, j)]
            c = sum(v.values())
            p, lo, hi = wilson_point_and_interval(c, len(v))
            row[s] = {"acc": round(p, 4), "ci": [round(lo, 4), round(hi, 4)], "n_correct": c, "n": len(v)}
        board[j] = row
    abst = {}
    for s in systems:
        c = sum(1 for k in adversarial_keys if e2e.is_noanswer(systems[s][k].get("predicted", "")))
        abst[s] = {"acc": round(c / len(adversarial_keys), 4), "n_correct": c, "n": len(adversarial_keys)}
    report["scoreboard"] = board
    report["abstention"] = abst

    # ---- pairwise McNemars ----
    pairs = [("provem", "mem0"), ("provem", "zep"), ("mem0", "zep")]
    mcn = {}
    for j in judges:
        for a, b in pairs:
            av = [verdicts[(a, j)][k] for k in answerable_keys]
            bv = [verdicts[(b, j)][k] for k in answerable_keys]
            x, y, nd, p = mcnemar_from_pairs(av, bv)
            mcn["%s:%s>%s" % (j, a, b)] = {"a_only": x, "b_only": y, "p": float("%.3g" % p)}
    for a, b in pairs:
        av = [e2e.is_noanswer(systems[a][k].get("predicted", "")) for k in adversarial_keys]
        bv = [e2e.is_noanswer(systems[b][k].get("predicted", "")) for k in adversarial_keys]
        x, y, nd, p = mcnemar_from_pairs(av, bv)
        mcn["abstention:%s>%s" % (a, b)] = {"a_only": x, "b_only": y, "p": float("%.3g" % p)}
    report["mcnemar"] = mcn

    # ---- per category (strict) + holdout ----
    cats = {}
    for code, label in sorted(CATEGORY_DISPLAY.items()):
        if label == "adversarial":
            continue
        keys = [k for k in answerable_keys if str(qinfo[k].category) == code]
        if not keys:
            continue
        cats[label] = {s: {"acc": round(sum(verdicts[(s, "strict")][k] for k in keys) / len(keys), 4),
                           "n": len(keys)} for s in systems}
    report["per_category_strict"] = cats
    hold = [k for k in answerable_keys if k[0] in HOLDOUT_CONVS]
    report["holdout_strict"] = {s: {"acc": round(sum(verdicts[(s, "strict")][k] for k in hold) / len(hold), 4),
                                    "n_correct": sum(verdicts[(s, "strict")][k] for k in hold), "n": len(hold)}
                                for s in systems}

    # ---- kappa strict vs opus (LLM-judged rows only: deterministic NO-ANSWER
    # rows agree trivially and would inflate the agreement) ----
    report["kappa_strict_vs_opus"] = {
        s: round(cohen_kappa([(verdicts[(s, "strict")][k], verdicts[(s, "opus")][k])
                              for k in answerable_keys
                              if not e2e.is_noanswer(systems[s][k].get("predicted", ""))]), 4)
        for s in systems}

    assert e2e._COST["calls"] == 0, "report made paid calls - forbidden"

    # ---- emit ----
    md = ["| Judge | Provem | Mem0 | Zep |", "|---|---|---|---|"]
    label = {"strict": "Strict binary (gpt-5)", "opus": "Cross-vendor (claude-opus-5)", "m0j": "Mem0's own judge prompt"}
    for j in judges:
        md.append("| %s | %.3f | %.3f | %.3f |" % (label[j], board[j]["provem"]["acc"], board[j]["mem0"]["acc"], board[j]["zep"]["acc"]))
    md.append("| Abstention (n=446) | %.3f | %.3f | %.3f |" % (abst["provem"]["acc"], abst["mem0"]["acc"], abst["zep"]["acc"]))
    report["markdown"] = "\n".join(md)

    out = json.dumps(report, indent=1, sort_keys=True)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(out + "\n")
        print("wrote %s" % args.out)
    print(report["markdown"])
    for k, v in sorted(mcn.items()):
        print("%-24s a_only=%-4d b_only=%-4d p=%g" % (k, v["a_only"], v["b_only"], v["p"]))
    print("kappa strict-vs-opus:", report["kappa_strict_vs_opus"])
    print("holdout:", {s: report["holdout_strict"][s]["acc"] for s in systems})


if __name__ == "__main__":
    main()
