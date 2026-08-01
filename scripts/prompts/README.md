# Prompt templates

## mem0_judge_template.txt

Mem0's official LoCoMo judge prompt (the no-evidence variant), fetched verbatim
from `github.com/mem0ai/memory-benchmarks` (`benchmarks/locomo/prompts.py`).
The `{question}` / `{answer}` / `{response}` placeholders are theirs.

The file is used byte-for-byte by `scripts/mem0_judge_bridge.py` — do not edit
or reformat it: provenance notes live here instead of inside the file because
the file content is sent verbatim to the judge model, and any change would
alter the "Mem0's own published judge prompt" scoring regime.

sha256: d248e056d993725e28fba8d16ca7081f0b59deae272ef294f3c6b00d48eac02b
