# External-reliability test fixtures

Tiny, checked-in samples used only by `tests/test_external_reliability.py` to
exercise the loaders and eval tracks offline. They are **not** the real
datasets — the full data is downloaded on demand into the git-ignored
`data/external/` (see `external_datasets.py`) and never committed.

| File | Shape mirrors | License of source | Notes |
|---|---|---|---|
| `deepset_mini.json` | deepset/prompt-injections (HF rows) | Apache-2.0 | 4 injection / 4 benign, hand-written analogues |
| `injecagent_mini.json` | InjecAgent tool-output attacks | MIT | 5 attacker instructions, hand-written analogues |
| `tofu_mini.json` | TOFU forget/retain QA | MIT | 2 fictitious authors x 3 forget + 3 retain |
| `ai4privacy_mini.json` | ai4privacy PII spans | self-authored | fake PII, `pii_status: fake` — no license risk |
| `manifest.json` | validation manifest | — | all `approved_for_eval: true`, `pii_status: fake` |

All text is written by us for testing; any resemblance to real payloads is only
structural. This keeps the fixtures free of licensing/PII concerns while still
proving the code paths.
