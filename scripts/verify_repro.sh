#!/bin/sh
# Self-checking EUR-0 replay of every published benchmark number.
#
# Every step re-derives numbers from the frozen artifacts and ASSERTS the exact
# published values; any missing input or drifted number exits non-zero. This is
# the regression gate behind the README's reproducibility promise — run it from
# the repo root. Pass --full to also replay the deterministic governance
# benchmark (~15 s) and the unit-test suite.
set -eu
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
TMPOUT=$(mktemp)
trap 'rm -f "$TMPOUT"' EXIT
ASSERTS=0
STEP=""

require() {
  for f in "$@"; do
    [ -f "$f" ] || { echo "MISSING INPUT: $f"; exit 1; }
  done
}

expect() {
  # expect <fixed-string> : assert the string occurs verbatim in the last step's output
  if ! grep -qF "$1" "$TMPOUT"; then
    echo "FAILED [$STEP]: expected verbatim <$1>"
    echo "---- step output ----"
    cat "$TMPOUT"
    exit 1
  fi
  ASSERTS=$((ASSERTS + 1))
}

run() {
  STEP="$1"; shift
  echo "== $STEP =="
  "$@" >"$TMPOUT" 2>&1 || { echo "FAILED [$STEP]: command exited non-zero"; cat "$TMPOUT"; exit 1; }
}

require \
  docs/runs/locomo_e2e_ours_vs_mem0.jsonl \
  docs/runs/locomo_e2e_ours_optimized.jsonl \
  docs/runs/locomo_e2e_ours_emptyfix.jsonl \
  docs/runs/locomo_e2e_mem0_patched.jsonl \
  docs/runs/iter/c2_full_final2.jsonl \
  docs/runs/iter/c2_v2_neutral.jsonl \
  docs/runs/zep_locomo.jsonl \
  docs/runs/caches/llm_cache_judge.jsonl \
  docs/runs/caches/judge2_cache.jsonl \
  docs/runs/caches/mem0_judge_cache.jsonl \
  docs/runs/caches/campaign_ledger.jsonl \
  docs/runs/manifest.json \
  scripts/prompts/mem0_judge_template.txt \
  data/external/locomo/locomo10.json

run "dataset integrity" shasum -a 256 data/external/locomo/locomo10.json
expect "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"

run "baseline (pre-optimization)" $PY scripts/mem0_e2e_report.py --in docs/runs/locomo_e2e_ours_vs_mem0.jsonl
expect "OURS 0.388 [0.364,0.412] (597/1540)"
expect "MEM0 0.475 [0.450,0.500] (731/1540)"
expect "p=5.18e-09"

run "optimized (dense,strict4)" $PY scripts/mem0_e2e_report.py --ours docs/runs/locomo_e2e_ours_optimized.jsonl
expect "OURS 0.536 [0.511,0.561] (826/1540)"
expect "p=4.23e-06"

run "post empty-fix (both sides fixed)" $PY scripts/mem0_e2e_report.py --ours docs/runs/locomo_e2e_ours_emptyfix.jsonl --mem0-baseline docs/runs/locomo_e2e_mem0_patched.jsonl
expect "OURS 0.611 [0.586,0.635] (941/1540)"

run "headline (final2 vs patched mem0)" $PY scripts/mem0_e2e_report.py --ours docs/runs/iter/c2_full_final2.jsonl --mem0-baseline docs/runs/locomo_e2e_mem0_patched.jsonl
expect "OURS 0.614 [0.590,0.638] (946/1540)"
expect "MEM0 0.509 [0.484,0.534] (784/1540)"
expect "McNemar: ours-only-right=317 mem0-only-right=155 discordant=472  p=7.19e-14"
expect "OURS 0.863 [0.828,0.892] (385/446)"
expect "MEM0 0.848 [0.811,0.878] (378/446)"

run "three-system scoreboard (three judges)" $PY scripts/zep_judges.py --workers 1
expect "zep judge top-up: 0 calls needed"
expect "strict gpt-5 judge        : provem 0.614 (946/1540) | mem0 0.509 (784/1540) | zep 0.449 (691/1540)"
expect "claude-opus-5 judge       : provem 0.502 (773/1540) | mem0 0.419 (646/1540) | zep 0.329 (506/1540)"
expect "Mem0's own judge prompt   : provem 0.772 (1189/1540) | mem0 0.722 (1112/1540) | zep 0.632 (974/1540)"
expect "abstention (446 adversarial): provem 0.863 (385/446) | mem0 0.848 (378/446) | zep 0.704 (314/446)"

run "neutral-prompt control (prompt-confound disclosure)" $PY - <<'EOF'
import json
rows = [json.loads(l) for l in open("docs/runs/iter/c2_v2_neutral.jsonl")]
adv = [r for r in rows if r.get("abstain")]
ans = [r for r in rows if not r.get("abstain")]
import sys
sys.path.insert(0, "src")
import importlib.util, os
spec = importlib.util.spec_from_file_location("e2e", "scripts/mem0_locomo_e2e.py")
e2e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e2e)
a = sum(1 for r in adv if e2e.is_noanswer(r.get("predicted", "")))
c = sum(1 for r in ans if r.get("correct"))
print("NEUTRAL abstention %.3f (%d/%d)" % (a / len(adv), a, len(adv)))
print("NEUTRAL answerable %.3f (%d/%d)" % (c / len(ans), c, len(ans)))
EOF
expect "NEUTRAL abstention 0.693 (309/446)"
expect "NEUTRAL answerable 0.596 (918/1540)"

run "manifest freshness" $PY scripts/build_manifest.py --check
expect "MANIFEST OK"

if [ "${1:-}" = "--full" ]; then
  run "governance benchmark (deterministic, no key)" env PYTHONPATH=src $PY -m cognitive_memory reliability --seeds 1,2,3,4,5,6,7,8,9,10 --scenarios 96
  expect "0.893 [0.872, 0.911]"
  expect "governed-only wins=497, ungoverned-only wins=0"
  expect "ungoverned 2.12 vs governed 0.00"
  run "unit tests" $PY -m pytest -q
  expect " passed"
fi

echo "VERIFY OK ($ASSERTS assertions)"
