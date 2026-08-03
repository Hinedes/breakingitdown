# BID Evaluation Evidence Index

Every empirical result recorded in `EVALUATION.md` has a corresponding
evidence directory under `/home/iclab/bid-eval/evidence/` on the evaluation
machine. Each directory contains the complete run state plus `SHA256SUMS`
for every file, so any artifact can be verified byte-for-byte.

The evaluation machine is the only location where evidence is stored; the
repo keeps this index so the evidence is *retrievable*: given an eval id,
the exact paths, hashes, and verification commands are defined here.

---

## Eval 0 — Real Smoke 0 (`ab08cba`)

- Evidence: `evidence/real-smoke-0-ab08cba-20260802/`
- Outcome: protocol operated end to end (3 tasks, COMPLETE); semantic false
  positive (test_greet.py passes Reviewer, pytest collects 0 tests)
- Key hashes: manifest sha `e43eeab5…` (in SHA256SUMS)
- Verify: `cd <dir> && sha256sum -c SHA256SUMS`
- Re-run oracle: `pytest test_greet.py` in the preserved workspace (expect
  "no tests ran")

## Eval 1 — Slice 0, BID-BENCH-002 (`ab08cba`, invalidated pair)

- Evidence: `evidence/benchmark-slice-0/bid-terminated/`
- Outcome: `BID terminated: Task Reviewer REWORK loop had no enforced
  attempt bound.` 2h50m / 31.2M tokens / 6 REWORKs on T1, zero provisionals.
  Pair invalidated; Direct not run.
- Verify: `cd <dir> && sha256sum -c SHA256SUMS`
- Re-run oracle: `ORACLE_ARGUS_DIR=<dir>/workspace-final /home/iclab/anaconda3/bin/python /home/iclab/bid-eval/benchmark-v2/hidden_oracle.py`

## Eval 2 — Slice 1, BID-BENCH-002 (`ab08cba-r2`)

- Evidence: `evidence/benchmark-slice-1/bid-terminated/` (BID),
  `evidence/benchmark-slice-1/direct-terminated/` (Direct)
- Outcome: BID bounded failure (17 min / 4.79M tokens / REWORK limit 4>3);
  Direct stalled (70 s, 0 changes). Oracle FAIL both. Direct fail / BID fail
  -> no demonstrated benefit; 16.4x token overhead.
- Verify: `sha256sum -c SHA256SUMS` in each directory
- Re-run oracles: `ORACLE_ARGUS_DIR=<dir>/workspace-final <oracle>` (same
  command as Eval 1)

## Eval 3 — Calibrated mechanism demonstration (v0.2.0-rc1)

- Evidence: `evidence/demo-calibrated/final/`
- Outcome: full loop to COMPLETE, external evaluator PASS 4/4
  (mean/median/stddev exact on `[1,2,3,4]`). Mechanism demonstration only;
  not a capability claim.
- Verify: `cd <dir> && sha256sum -c SHA256SUMS`
- Re-run evaluator: `DEMO_WS=<dir>/workspace-final /home/iclab/anaconda3/bin/python <dir>/evaluator.py`

---

## Freeze / task assets (not run evidence, but frozen inputs)

- Benchmark v2 task + oracle: `/home/iclab/bid-eval/benchmark-v2/`
  - `task.txt` sha `971dc0b9…`, `hidden_oracle.py` sha `6b610508…`
- Freeze docs: `evidence/benchmark-slice-0/FREEZE.md`,
  `evidence/benchmark-slice-1/FREEZE.md`
- Demo task + evaluator: `evidence/demo-calibrated/task.txt`,
  `evidence/demo-calibrated/evaluator.py`

## Model identity

- `bid-qwen3.5:4b-ctx64k`, derived digest
  `c727d70306002e9d13bd0ad52e9da8d890ed532d9a75ffafa21ce2ac9ddec6ca`
- Base digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`
- Context 65536, GPU-resident during all runs

## Conventions

- Evidence directories are immutable after the run they capture; never edit
  in place. A re-run creates a new directory.
- Every evidence directory ends with `SHA256SUMS`; regenerate only when
  adding new artifacts, then re-verify the whole tree.
- The decision matrix and honest-limitations rules live in `EVALUATION.md`.
