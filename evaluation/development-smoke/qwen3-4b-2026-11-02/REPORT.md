# Qwen3-4B CLI development treatment — 2026-11-02

This is a separately labeled, bounded product-development check. It is not a
benchmark, an architecture comparison, or a replacement for historical Llama
studies. No historical study data was changed.

## Model and runtime

- `Qwen3-4B-Instruct-2507-Q8_0.gguf`, pinned to
  `bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF` revision
  `ae44f08e1392f39c0e474af10c3ff8355c8b6688`.
- Downloaded size: 4,280,405,216 bytes; verified SHA-256:
  `260b5b5b6ad73e44df81a43ea1f5c11c37007b6bac18eb3cd2016e8667c19662`.
  Base model license: Apache-2.0.
- One llama.cpp server on localhost:8081, one slot, requested and allocated
  context 65,536, Q8_0 K/V, 99 GPU layers requested. Runtime commit
  `8642541b255bfb5dc39cd351a178be09013a55d6`, RTX 5070 Ti. The log reports
  4,896 MiB KV (2,448 MiB each K and V) and 65,536 context cells. `nvidia-smi`
  reported 9,986 MiB used after runs.
- CLI used ordinary prompts/parser: optional grammar off, host RUN disabled,
  max searches 0, 2,048 token ceiling, batch 1. Raw request/response traces
  and source manifests are preserved alongside each outcome.
- The pinned base tokenizer template contains no `enable_thinking` variable
  and renders a plain assistant prefix. Existing client metadata setting
  `enable_thinking=true` was therefore left unchanged; no think token was
  injected. Startup log's `thinking=1` detector output is preserved as-is.

## Outcomes

| Run | Result |
|---|---|
| `run-001-hello` | Artifact bytes were correct and Reviewer accepted, but Manager reconciliation repeatedly returned inline headings (`# Done: - T1`); BID correctly did not ratify. Failure retained. |
| `run-002-hello` | Pass. Exact `hello.txt` bytes `hello from BID\n`; Reviewer accepted; task `[x]`; provisional and ratified records persisted. 1 Manager / 4 Worker / 1 Reviewer / 2 reconciliation requests. |
| `run-003-dependency` | Two dependent tasks were planned. Reviewer rejected an initial `main.py` missing the import; the retry still emitted an invalid module call, and Manager repeated an already-ratified task ID. BID did not ratify the batch. Failure retained. |
| `run-004-dependency` | Pass. `greeting.py` and importing `main.py` completed through two Worker/Reviewer/reconciliation handoffs; both tasks `[x]`, both ratified. Independent `python main.py` returned 0 and printed exactly `Hello, BID!\n`. 1 Manager / 4 Worker / 2 Reviewer / 3 reconciliation requests. |

The two prompt corrections were generic: reconciliation headings/entries were
made explicit, and reconciliation was told to use only IDs in the active
provisional submissions (not previously checked tasks). The dependency task
specified the function import explicitly after the first trace exposed an
ambiguous module-vs-function import. No grammar or forced acceptance was used.

## Source and evidence

- Linux source snapshots are preserved remotely as `source/`, `source-v2/`,
  and `source-v3/`; local manifest copies are `source-manifest.sha256`,
  `source-v2-manifest.sha256`, and `source-v3-manifest.sha256`.
- `run-001-hello` used source manifest SHA-256
  `a06696dfb6fedc41c25038573e2c9f0a2d82cac07eeb76e8498cf7c0e17a11ba`;
  `run-002-hello` used
  `64ac4081a043b943979dc97d3f3bc9db2f53a2ddf981763b3f50ebf8409a2c38`.
  `run-003-dependency` and `run-004-dependency` used
  `dfd07d0dddb12d3e9be20bd1d30289e8e7c34a820cbea2eb1adcdd371f7f1ba3`.
- Independently re-read persisted workspace state and reran the dependency
  executable check: `independent-verification.json`.
- Runtime launch command/settings, server logs, GPU samples, all raw traces,
  outcomes, and failure workspaces are retained in this treatment directory.

## Post-run primary review

Review found that Worker cycle detection stalled on the first return to any
earlier workspace state. The current checkout now counts repeated state
revisits against `BID_REPEAT_ACTION_LIMIT`; a new state or useful evidence
resets the count. The regression tests cover a legitimate write/revert/submit
and a bounded alternating-write cycle with rollback. Snapshot signatures still
reuse Observer's tracked snapshot.

This correction was made after source-v3 and after all four runs above. The
current Windows suite passes **185** tests with **10** skips; the recorded Linux
**194 passed** result belongs to source-v3, before the correction. Primary later
verified a separate corrected-source snapshot through an ordinary Qwen CLI
run. Its manifest, arithmetic task, isolated checks, and runtime record are at
`evaluation/development-smoke/primary-verification-2026-11-02/README.md` and
`outcome.json`. That single task does not establish general task reliability or
an architecture benefit. The README recipe reproduces the earlier recorded
server and CLI settings; it does not replace the separate verification record.
