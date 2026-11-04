# v1 freeze (2026-11-03)

BID source: git `96e9ce113f3a04d19e748b7dd8bd0f01b22394a5` (`v1/merge`), tree hash
`7112ff6f3c57ce76225902b41526237f7a46d34f6ce4f67e6f143d8f3ff0e5d1` (pilot.source_tree_hash over
`bid/`, `prompts/`, `pyproject.toml`, `evaluation/swebench-pilot/*.py`, LF line endings).
Previous frozen source `c63701b9...` (manifest `b0cf7f6d...`) is recorded in the manifest pins.
Manifest `manifest-7112ff6f3c57.json`, self-hash `548464f7d9beaaa96f8e3f725750e4f441472fa57c36830006a6aedf3cf440a4`;
same sample, budgets, model, evaluator and task-repo pins as before, only the source pin changed.

Deployed to Q12 as `bid-source-7112ff6f3c57` (tar sha256 `e13b64e8...`). Run directory `runs-v1`,
runtime `runtime/v1-2026-11-03`. Gold/base gate record reused (same 20 instances and task images).
Task images: 13 retained + 7 rebuilt from cached layers; 20 present before launch.
`bid_swe_v1.py` is the supervised launcher (adapted from the earlier remainder launcher: pauses on any
infrastructure fault, no retries). `--limit N` runs the first N queue cells.
