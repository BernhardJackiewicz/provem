"""Analyze the end-to-end LoCoMo results JSONL: paired accuracy, McNemar, CIs.

Both systems answer the SAME questions, so the correct significance test is the
paired McNemar test on discordant pairs; the effect size gets a paired bootstrap CI.
"""
import argparse
import json
import sys

sys.path.insert(0, "src")
from cognitive_memory.stats import mcnemar_from_pairs, wilson_point_and_interval, bootstrap_diff_ci


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="/private/tmp/claude-501/-Users-bernhard-Desktop-brain/e9f97b9f-09f0-4aff-ac11-a991e6b1aafa/scratchpad/e2e_results.jsonl")
    args = ap.parse_args()

    rows = {}
    for line in open(args.inp):
        try:
            r = json.loads(line)
        except Exception:
            continue
        rows[(r["conv"], r["qid"], r["system"])] = r

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
