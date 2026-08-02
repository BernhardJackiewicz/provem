"""Regenerate docs/runs/manifest.json from a declared artifact list.

The manifest used to be hand-maintained and drifted (the headline artifacts
were missing from its hash list). This script is the single source of truth:
--write regenerates the manifest, --check recomputes every hash and exits
non-zero on any drift, so verify_repro.sh can gate on it.

Artifacts come in two classes:
  ARTIFACTS  — distributed with the repo; hashes must always verify.
  LOCAL_ONLY — not redistributed (dataset license / size); hashed when present,
               skipped with a note when absent (e.g. in a fresh clone before
               scripts/fetch_locomo.sh ran).
"""
import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys

MANIFEST = "docs/runs/manifest.json"

ARTIFACTS = sorted(
    [
        "docs/runs/locomo_e2e_ours_vs_mem0.jsonl",
        "docs/runs/locomo_e2e_ours_optimized.jsonl",
        "docs/runs/locomo_e2e_ours_emptyfix.jsonl",
        "docs/runs/locomo_e2e_mem0_patched.jsonl",
        "docs/runs/zep_locomo.jsonl",
        # v2 corrected re-measurement (headline)
        "docs/runs/locomo_e2e_mem0_v2.jsonl",
        "docs/runs/zep_locomo_v2.jsonl",
        "docs/runs/three_system_report_v2.json",
        "docs/runs/caches/campaign_ledger.jsonl",
        "docs/runs/caches/remeasure_ledger.jsonl",
        "docs/runs/caches/judge2_cache.jsonl",
        "docs/runs/caches/llm_cache_answer.jsonl",
        "docs/runs/caches/llm_cache_judge.jsonl",
        "docs/runs/caches/mem0_judge_cache.jsonl",
        "scripts/prompts/mem0_judge_template.txt",
    ]
    + glob.glob("docs/runs/iter/*.jsonl")
)

LOCAL_ONLY = [
    # CC BY-NC 4.0 — obtained via scripts/fetch_locomo.sh, never redistributed here
    "data/external/locomo/locomo10.json",
    # 109 MB embeddings cache (Git LFS); only needed to re-run dense retrieval,
    # every published-number replay works without it
    "docs/runs/caches/vector_cache.jsonl",
]

STATIC = {
    "purpose": "Reproducibility freeze for the three-system LoCoMo benchmark (Provem vs Mem0 vs Zep) and its campaign history",
    "dataset": {
        "file": "data/external/locomo/locomo10.json",
        "convs": 10,
        "qa": 1986,
        "answerable": 1540,
        "adversarial": 446,
    },
    "dev_convs": [0, 2, 4, 6, 8],
    "holdout_convs": [1, 3, 5, 7, 9],
    "top_k": 20,
    "models": {
        "answerer": "gpt-5-mini (reasoning_effort=medium)",
        "primary_judge": "gpt-5 (reasoning_effort=low)",
        "second_judge": "claude-opus-5",
        "mem0_own_judge": "gpt-5 with Mem0's published prompt (scripts/prompts/mem0_judge_template.txt)",
        "embeddings": "text-embedding-3-small",
    },
    "models_note": "Model ids are provider aliases; exact snapshot ids were not recorded for the original (v1) runs. New measurement campaigns record snapshots below.",
    "model_snapshots": {
        "v2_remeasurement_2026-08-01": {
            "answerer": "gpt-5-mini-2025-08-07",
            "primary_judge": "gpt-5-2025-08-07",
            "second_judge": "claude-opus-5 (Anthropic returns the id as requested)",
            "probe": "1-token probe calls before phase M1; see docs/measurement_changelog.md",
        }
    },
    "headline_version": "v2 (corrected Mem0+Zep re-measurement, 2026-08-02); v1 numbers archived in docs/measurement_changelog.md",
    "prompt_versions": {
        "answerer_default": "v1",
        "strict_chain": ["strict1", "strict2", "strict3", "strict4", "strict4agg", "strict5", "strict5agg"],
        "judge": "JUDGE_SYS in scripts/mem0_locomo_e2e.py (shared verbatim by both judges)",
    },
    "known_issue_fixed": "empty content with finish_reason=length was scored wrong pre-fix; both systems patched symmetrically (see lager_optimization_log.md)",
    "judge_cache_note": "llm_cache_judge.jsonl was backfilled from the stored per-row verdicts (scripts/backfill_judge_cache.py); stored rows are the published record",
    "replay": "sh scripts/verify_repro.sh replays and ASSERTS every headline number at EUR 0 from the frozen artifacts",
    "three_system_scoreboard": {
        "note": "v2 corrected re-measurement; source docs/runs/three_system_report_v2.json",
        "strict_gpt5": {"provem": 0.614, "mem0": 0.565, "zep": 0.449},
        "claude_opus_5": {"provem": 0.502, "mem0": 0.368, "zep": 0.304},
        "mem0_own_judge": {"provem": 0.772, "mem0": 0.716, "zep": 0.649},
        "abstention": {"provem": 0.863, "mem0": 0.830, "zep": 0.722},
        "zep_config": "per Zep's published checklist; chronological ingestion; full graph completion hard-gated before eval",
    },
    "three_system_scoreboard_v1_superseded": {
        "strict_gpt5": {"provem": 0.614, "mem0": 0.509, "zep": 0.449},
        "note": "v1 had a Mem0 double-date input bug and Zep lexical ingestion order; see docs/measurement_changelog.md",
    },
}


def sha256(path):
    # A Git-LFS pointer file carries the real content's sha256 as its oid;
    # verifying against it lets --check pass in clones that have not run
    # `git lfs pull` (the pointer proves what the content would be).
    with open(path, "rb") as fh:
        head = fh.read(200)
    if head.startswith(b"version https://git-lfs"):
        for line in head.decode("utf-8", "replace").splitlines():
            if line.startswith("oid sha256:"):
                return line.split("oid sha256:", 1)[1].strip()
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build():
    doc = dict(STATIC)
    try:
        doc["git_sha_at_build"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        doc["git_sha_at_build"] = "unknown"
    missing = [p for p in ARTIFACTS if not os.path.exists(p)]
    if missing:
        raise SystemExit("MANIFEST FAIL: distributed artifacts missing: %s" % ", ".join(missing))
    doc["artifacts_sha256"] = {p: sha256(p) for p in ARTIFACTS}
    doc["local_only_sha256"] = {
        p: (sha256(p) if os.path.exists(p) else "absent")
        for p in LOCAL_ONLY
    }
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.write == args.check:
        raise SystemExit("pass exactly one of --write / --check")

    doc = build()
    if args.write:
        with open(MANIFEST, "w") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("MANIFEST WRITTEN (%d artifacts, %d local-only)" % (
            len(doc["artifacts_sha256"]), len(doc["local_only_sha256"])))
        return

    current = json.load(open(MANIFEST))
    drift = []
    for p, h in doc["artifacts_sha256"].items():
        cur = current.get("artifacts_sha256", {}).get(p)
        if cur != h:
            drift.append("%s: manifest=%s actual=%s" % (p, cur, h))
    for p in current.get("artifacts_sha256", {}):
        if p not in doc["artifacts_sha256"]:
            drift.append("%s: in manifest but not in declared list" % p)
    for p, h in doc["local_only_sha256"].items():
        cur = current.get("local_only_sha256", {}).get(p)
        if h == "absent":
            print("note: local-only artifact absent (ok in fresh clone): %s" % p)
        elif cur != h:
            drift.append("%s (local-only): manifest=%s actual=%s" % (p, cur, h))
    if drift:
        print("MANIFEST DRIFT:")
        for d in drift:
            print("  " + d)
        sys.exit(1)
    print("MANIFEST OK (%d artifacts verified)" % len(doc["artifacts_sha256"]))


if __name__ == "__main__":
    main()
