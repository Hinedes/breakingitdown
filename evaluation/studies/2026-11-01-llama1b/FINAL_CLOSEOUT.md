# Overnight experiment closeout — 2026-11-02

## Decision

The hard cutoff was 08:00 Taipei. No inference, new matrix, calibration, or
product-code modification was made during closeout. Epoch 18 remained stopped
at 17 completed outcomes, one interrupted outcome, and 27 pending. Epoch 20
was planned, not launched. There is no completed new comparative cohort and
no native coding-agent baseline.

Do not pool repeated seeds across epochs as independent samples. Artifact
correctness, harness completion, and baseline-seed checks remain separate
quantities: a passing artifact oracle does not establish harness completion,
and checks already true in a seed workspace are not model-produced progress.

## Immutable candidate and checks

- Epoch-20 source manifest SHA-256: `e925ca9e3d3e6c3c6e9cb6f9be19e04aa9f171bffd74ba4e6299d0e12d20c051` (36 files). The primary independently verified this remote manifest.
- Recorded candidate broad suites: Ubuntu **197 passed**; Windows **175 passed, 10 capability-specific skips**. The Windows run performed during this closeout was **175 passed, 10 skipped in 14.82s**; see command/results in the audit ledger.
- `git diff --check` passed; only whitespace warnings were Git's existing LF-to-CRLF notices for working-tree files.
- Primary independently ran focused tests against the immutable epoch-20 source:

  ```text
  test-venv-epoch-10/bin/python -m pytest -q -p no:cacheprovider tests/test_symlink_boundaries.py <epoch20/source/evaluation/studies/2026-11-01-llama1b/test_study.py> -k 'symlink or named_pipe or worker_executor or artifact_hashes'
  9 passed, 9 deselected in 1.48s
  ```

  Coverage included actual Linux symlink/FIFO behavior, persistent worker
  execution, session-detaching timeout cleanup, and artifact hashes. This is a
  focused regression check, not a broad security certification.

## Boundary and timeout corrections

The source correction treats model-created links as link entries during
host-side bookkeeping, snapshots/restores, observation, review, and hashing;
special files are rejected or skipped rather than opened, and symbolic
control roots are rejected before initialization. The trusted namespace PID 1
owns model commands as child processes so a command that detaches its session
cannot outlive parent timeout cleanup. This addresses the observed tested
paths only; it is not a claim of comprehensive filesystem-race protection,
kernel escape resistance, or aggregate resource limits.

## Final machine state

At 09:52 Taipei the primary verified no active study processes. Owned
llama-server PID 8611 matched the known binary, Llama 3.2 3B Q8_0 model, and
localhost:8081 endpoint; it was stopped gracefully with SIGTERM. No
llama-server remained. GPU afterward: **391 MiB used / 15,605 MiB free** of
16,303 MiB. See primary-created [`FINAL_RUNTIME_STATE.json`](FINAL_RUNTIME_STATE.json);
it is not edited by this report.

## Evidence provenance and limits

The delegated evidence-export attempt did not complete in its execution
environment; its cause is not established here. The primary successfully used
SSH and remote Ubuntu-22.04 to verify the epoch-20 manifest and focused Linux
checks, and independently verified runtime shutdown. Remote `SAFETY_STOP.json`,
summaries, and calibration outcomes were not exported into this checkout; the
raw epoch-18 queue was not recomputed. The 17/1/27 counts remain recorded from
the closeout instruction and local `MATRIX20_PLAN.md`, not newly verified here.
No home scan or traversal of model-created links was attempted.

Existing local provenance remains available: `SOURCE_MANIFEST.epoch-9.json`
through `SOURCE_MANIFEST.epoch-13.json`, and the calibration outcome files
under `calibration-evidence/runs-epoch-*`. `CALIBRATION_REPORT.md` summarizes
those prior exploratory probes; none is a comparative cohort or a native-agent
baseline.
