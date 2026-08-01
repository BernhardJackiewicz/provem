"""Backfill the strict-judge disk cache from the stored per-row verdicts.

The published prediction files carry the authoritative strict-judge verdict in
every row ("correct"), but the frozen cache (llm_cache_judge.jsonl) predates
parts of the campaign: 755 of Mem0's non-abstain verdicts were missing and 7
cached entries contradicted the stored rows. A replay that re-judges from the
cache would therefore drift from the published record.

Resolution rule (documented in docs/measurement_changelog.md): the stored
per-row verdicts ARE the published record; the cache is backfilled to match.
DiskCache is append-only with last-line-wins semantics, so appending the stored
verdict makes it authoritative without rewriting history. Idempotent: a second
run appends nothing.
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

# Chronological campaign order; if the same (qid, prediction) was live-judged
# twice with different outcomes, the later (published) file wins.
FILES = [
    ("docs/runs/locomo_e2e_ours_vs_mem0.jsonl", ("ours", "mem0")),
    ("docs/runs/locomo_e2e_ours_optimized.jsonl", ("ours",)),
    ("docs/runs/locomo_e2e_ours_emptyfix.jsonl", ("ours",)),
    ("docs/runs/locomo_e2e_mem0_patched.jsonl", ("mem0",)),
    ("docs/runs/iter/c2_full_final2.jsonl", ("ours",)),
    ("docs/runs/zep_locomo.jsonl", ("zep",)),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="docs/runs/caches/llm_cache_judge.jsonl")
    ap.add_argument("--judge-model", default="gpt-5")
    ap.add_argument("--judge-effort", default="low")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # stored record: key -> (verdict, provenance)
    desired = {}
    stored_conflicts = []
    n_rows = 0
    for path, wanted_systems in FILES:
        if not os.path.exists(path):
            raise SystemExit("missing prediction file: %s" % path)
        for line in open(path):
            r = json.loads(line)
            if r.get("system") not in wanted_systems:
                continue
            if r.get("abstain") or r.get("error"):
                continue  # abstention is deterministic; error rows were never judged
            pred = r.get("predicted", "")
            if e2e.is_noanswer(pred):
                continue  # NO-ANSWER on answerable is deterministically wrong, never judged
            key = e2e._h("jud", r["qid"], args.judge_model, args.judge_effort,
                         (pred or "").strip().lower())
            verdict = bool(r["correct"])
            prov = "%s:%s:conv%s:%s" % (os.path.basename(path), r["system"], r["conv"], r["qid"])
            if key in desired and desired[key][0] != verdict:
                stored_conflicts.append((desired[key][1], prov))
            desired[key] = (verdict, prov)
            n_rows += 1

    cache = e2e.DiskCache(args.cache)
    missing, conflicting, consistent = [], [], 0
    for key, (verdict, prov) in desired.items():
        cur = cache.get(key)
        if cur is None:
            missing.append((key, verdict, prov))
        elif bool(cur) != verdict:
            conflicting.append((key, bool(cur), verdict, prov))
        else:
            consistent += 1

    print("stored rows considered : %d (%d unique judge keys)" % (n_rows, len(desired)))
    print("cache consistent       : %d" % consistent)
    print("cache missing          : %d" % len(missing))
    print("cache conflicting      : %d (cache verdict != stored verdict)" % len(conflicting))
    for key, cached_v, stored_v, prov in conflicting:
        print("  CONFLICT %s cached=%s stored=%s" % (prov, cached_v, stored_v))
    if stored_conflicts:
        print("stored-vs-stored re-judgments (later file wins): %d" % len(stored_conflicts))
        for a, b in stored_conflicts:
            print("  REJUDGED %s -> %s" % (a, b))

    if args.dry_run:
        print("dry-run: cache untouched")
        return
    for key, verdict, _prov in missing:
        cache.put(key, verdict)
    for key, _cached_v, stored_v, _prov in conflicting:
        cache.put(key, stored_v, overwrite=True)  # put() silently no-ops on existing keys otherwise
    print("appended %d entries (%d backfills, %d overrides); cache now authoritative for the stored record"
          % (len(missing) + len(conflicting), len(missing), len(conflicting)))


if __name__ == "__main__":
    main()
