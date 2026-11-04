# Strict-protocol calibration results

These are separate exploratory treatments after immutable epoch 9. They are
calibration probes, not task benchmark cells or architecture comparisons.

| Run | Model / seed | Requests; tokens; wall | Coverage | Result |
|---|---|---:|---|---|
| Epoch 10, calibration-2 | Llama 3.2 1B F16; `20261102` | 2 Manager, 35 Worker; 29,564 tokens; 5.695 s | 0 Reviewer verdicts, 0 Manager decisions | Failed. The Manager's first response was malformed; retry passed four generic `Description` placeholders. Worker followed unrelated paths and exhausted its token budget. No final task files. |
| Epoch 11, calibration-1 | Llama 3.2 3B Q8_0; Q8_0 K/V; 65,536 context; `20261103` | 1 Manager, 32 Worker; 29,380 tokens; 6.850 s | 0 Reviewer verdicts, 0 Manager decisions | Failed. Manager did not preserve literal content. Worker invented a different file body and looped on malformed `REPLACE`. No final task files. |
| Epoch 12, calibration-1 | Same 3B runtime; `20261104` | 1 Manager, 25 Worker; 28,473 tokens; 3.545 s | 0 Reviewer verdicts, 0 Manager decisions | Failed. Manager preserved the requested string, and Worker wrote four bytes (`MINT`) but omitted the newline. Worker then repeated `READ` with an invalid `END READ` marker; this error prevented submission and the harness reset the unsubmitted change. No final task files. |

All three model runs ended at the accounted-token boundary (`token_cap_prompt`),
not the external wall timeout. GNU `timeout` supervised each generation process
group with a 310-second cap for the 3B runs and 330 seconds for the 1B run;
each supervisor exited normally and no generation remained active between
runs. Epoch 10 calibration-1 is also retained as a runner-startup failure
(`ModuleNotFoundError: study`, zero model requests), separate from the valid
calibration-2 outcome.

## Model/runtime provenance

- 1B: F16 model SHA-256
  `1f33ad43d2b85b908ff06fe7002b69806a57359b9b2617ca27d7bdea428ae146`.
- 3B: `bartowski/Llama-3.2-3B-Instruct-GGUF`, revision
  `5ab33fa94d1d04e903623ae72c95d1696f09f9e8`, Q8_0 weights file SHA-256
  `b5607b5090a8280063fff2d706bb3408ca6542341b06aab39c3eca0a28575921`.
  Q8_0 K/V cache, one slot, effective context 65,536. After load, the RTX
  5070 Ti reported 8,056 MiB used and 7,940 MiB free. F16 3B weights plus an
  F16 64K cache were not attempted because the estimated memory left too little
  headroom; the selected quantization and context are explicit. Server launch
  settings and log are preserved in `calibration-evidence/`; server log SHA-256
  is `ac483c8ea5fb05f4a4a964308f0cfdf2453c2ee0bd14b674eb39fb8afa4127ec`.
  The owned server was stopped after the calibration; GPU memory returned to
  391 MiB used and 15,605 MiB free.
- Per-run generation seeds were passed in each generation request and token
  preflight: 20261102, 20261103, and 20261104. RUN remained disabled; no
  generated code was executed.

## Interpretation

The full Manager → Worker → Reviewer → Manager loop was not reached in any
calibration. Epoch 12 transiently wrote the requested file but did not submit
it, and its final workspace contained no task artifact. This does not establish
architecture behavior. No batch/context ablation, execution-enabled condition,
or native-agent comparison was run. The 3B calibration is its own cohort and
must not be pooled with 1B or epoch 9.

RUN stays denied. The existing oracle sandbox uses a workspace copy and does
not persist Worker RUN edits, so execution-enabled agents were not exercised.

## Evidence and checks

- Per-run outcomes, full response traces, workspace/task state, Worker command
  logs, and supervisor sidecars are in `calibration-evidence/`.
- Immutable source manifests: `SOURCE_MANIFEST.epoch-10.json`,
  `SOURCE_MANIFEST.epoch-11.json`, and `SOURCE_MANIFEST.epoch-12.json`. Their
  hashes are respectively `0cd58a88900036dad98e0bc61417b6809ab9e0b847a9293ddda51d4881ee0c20`,
  `8c5694341970ab3989dcd535e66b49bbdd75601c715b3a7bf6081653a71ce591`, and
  `8fac8838121cc1713a103b29ceda4c4bf234940888b7fd83faddc9141a55da39`. Each
  outcome records its matching source manifest hash.
- Ubuntu 22.04, Python 3.12: epoch-12 full suite **175 passed**; focused study
  tests **6 passed**. Epoch-13 runner-only source: full suite **175 passed**;
  focused study tests, including descendant-process cleanup, **7 passed**.
  Windows local suite: **171 passed, 4 skipped**; Linux-only process/sandbox
  cases skip there.
- Epoch 13 is runner hardening only, with no new model cells. It adds a
  parent-owned POSIX process-group supervisor, hard wall termination and
  leftover cleanup, atomic outcome writes, partial-workspace checkpoint hashes,
  setup-cost separation, a 100,000-token equal per-cell default, configurable
  model identity/cache metadata, and separate logical/cached token accounting.
  Its immutable source manifest SHA-256 is
  `906eaf8a5832b23af939ec47e3eeb91565a0be91987f1ac03badbc8da32c37d6`.
- No staging, commit, or push was performed. Epochs 9 through 12 and their
  outcomes remain separate and immutable; epoch 13 has no model outcomes.
- The separate Eval 5 historical pair was replayed in the rootless sandbox;
  verification and limits are documented in `HISTORICAL_REPLAY_REPORT.md`.
- The bounded RUN sandbox assessment passed isolation and cleanup probes but
  confirmed there is no persistent, validated Worker artifact transfer path.
  RUN remains denied; see `RUN_SANDBOX_ASSESSMENT.md`.
