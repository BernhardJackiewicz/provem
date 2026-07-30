"""Analyze the end-to-end LoCoMo results JSONL: paired accuracy, McNemar, CIs.

Both systems answer the SAME questions, so the correct significance test is the
paired McNemar test on discordant pairs; the effect size gets a paired bootstrap CI.
"""
import argparse
import json
import sys

sys.path.insert(0, "src")
from cognitive_memory.stats import mcnemar_from_pairs, wilson_point_and_interval, bootstrap_diff_ci


def _load_rows(path, rows, only_system=None):
    for line in open(path):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if only_system and r["system"] != only_system:
            continue
        rows[(r["conv"], r["qid"], r["system"])] = r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=None, help="combined run (both systems in one file)")
    ap.add_argument("--ours", default=None, help="ours-only iteration run (JSONL)")
    ap.add_argument("--mem0-baseline", default="docs/runs/locomo_e2e_ours_vs_mem0.jsonl",
                    help="frozen baseline run supplying the mem0 rows")
    ap.add_argument("--convs", default=None, help="restrict to comma-separated conv indices (e.g. DEV slice)")
    args = ap.parse_args()

    rows = {}
    if args.inp:
        _load_rows(args.inp, rows)
    else:
        if not args.ours:
            raise SystemExit("need --in or --ours")
        _load_rows(args.mem0_baseline, rows, only_system="mem0")
        _load_rows(args.ours, rows, only_system="ours")

    if args.convs:
        keep = {int(x) for x in args.convs.split(",")}
        rows = {k: v for k, v in rows.items() if k[0] in keep}

    # pair by (conv, qid)
    keys = sorted({(c, q) for (c, q, s) in rows})
    paired_ans = []  # (ours_correct, mem0_correct) for answerable
    paired_ab = []
    cat = {}
    conv = {}
    for (c, q) in keys:
        ro = rows.get((c, q, "ours")); rm = rows.get((c, q, "mem0"))
        if ro is None or rm is None:
            continue
        pair = (bool(ro["correct"]), bool(rm["correct"]))
        if ro.get("abstain"):
            paired_ab.append(pair)
        else:
            paired_ans.append(pair)
            cat.setdefault(ro["category"], []).append(pair)
            conv.setdefault(c, []).append(pair)

    def summ(pairs, label):
        n = len(pairs)
        if n == 0:
            return
        oc = sum(1 for a, b in pairs if a); mc = sum(1 for a, b in pairs if b)
        op, olo, ohi = wilson_point_and_interval(oc, n)
        mp, mlo, mhi = wilson_point_and_interval(mc, n)
        b, cc, nd, p = mcnemar_from_pairs([a for a, _ in pairs], [x for _, x in pairs])
        obs, lo, hi = bootstrap_diff_ci([1.0 if a else 0.0 for a, _ in pairs],
                                        [1.0 if x else 0.0 for _, x in pairs], iterations=5000)
        print("\n%s (n=%d)" % (label, n))
        print("  OURS %.3f [%.3f,%.3f] (%d/%d)   MEM0 %.3f [%.3f,%.3f] (%d/%d)"
              % (op, olo, ohi, oc, n, mp, mlo, mhi, mc, n))
        print("  diff (ours-mem0) = %+.3f  95%% CI [%+.3f,%+.3f]" % (obs, lo, hi))
        print("  McNemar: ours-only-right=%d mem0-only-right=%d discordant=%d  p=%.3g" % (b, cc, nd, p))

    summ(paired_ans, "ANSWERABLE accuracy")
    summ(paired_ab, "ABSTENTION accuracy (adversarial/no-info)")

    CATN = {"1": "multi-hop", "2": "single-hop", "3": "temporal", "4": "open-domain", "5": "adversarial"}
    print("\n-- by category (raw code / harness name) --")
    for ccode in sorted(cat):
        pairs = cat[ccode]; n = len(pairs)
        oc = sum(1 for a, _ in pairs if a); mc = sum(1 for _, b in pairs if b)
        print("  cat %s %-12s n=%3d  OURS %.3f (%d)  MEM0 %.3f (%d)"
              % (ccode, CATN.get(ccode, "?"), n, oc / n, oc, mc / n, mc))

    print("\n-- by conversation --")
    for c in sorted(conv):
        pairs = conv[c]; n = len(pairs)
        oc = sum(1 for a, _ in pairs if a); mc = sum(1 for _, b in pairs if b)
        print("  conv %d n=%3d  OURS %.3f (%d)  MEM0 %.3f (%d)" % (c, n, oc / n, oc, mc / n, mc))


if __name__ == "__main__":
    main()
