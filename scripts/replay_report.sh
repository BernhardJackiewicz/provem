#!/bin/sh
# Reproduce every headline number of the Engram-vs-Mem0 comparison at EUR 0.
# All LLM calls are served from the frozen disk caches (docs/runs/caches/);
# nothing is paid and nothing changes. See docs/runs/manifest.json.
set -e
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
echo "== BASELINE (pre-optimization): ours vs mem0, judge gpt-5 =="
$PY scripts/mem0_e2e_report.py --in docs/runs/locomo_e2e_ours_vs_mem0.jsonl | head -10
echo ""
echo "== OPTIMIZED (dense,strict4): ours vs frozen mem0, judge gpt-5 =="
$PY scripts/mem0_e2e_report.py --ours docs/runs/locomo_e2e_ours_optimized.jsonl | head -10
for f in docs/runs/locomo_e2e_ours_emptyfix.jsonl; do
  if [ -f "$f" ]; then
    echo ""
    echo "== POST EMPTY-FIX: ours vs patched mem0 (both sides fixed) =="
    $PY scripts/mem0_e2e_report.py --ours "$f" --mem0-baseline docs/runs/locomo_e2e_mem0_patched.jsonl | head -10
  fi
done
echo ""
echo "== campaign ledger =="
[ -f docs/runs/caches/campaign_ledger.jsonl ] && cat docs/runs/caches/campaign_ledger.jsonl | tail -20

echo ""
echo "== THREE-SYSTEM SCOREBOARD: Provem vs Mem0 vs Zep (from frozen judge caches) =="
$PY scripts/zep_judges.py --workers 1 2>/dev/null | tail -6
