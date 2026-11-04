# Canary audit - 3 cells, `django__django-10097`

Date: 2026-11-02. Doc/evidence only. No source, prompt, manifest, model or runtime
change was made; no scored cell was started by this audit.

Pins in force: source `f4999e9656a553321af77c2111340ac80c6899be1afe84a1e0d708d31a828f23`,
manifest `9e662f1174352ffe349fd9e14ef0957aa8bee4308e3c502fcc56d572c995f0c0`.

Artifacts in this directory: `audit.json` (all numbers below),
`controller-status.json` (bounded controller records), `result.json` (the
original main-owned wrapper result, untouched), this report.

## Cell status semantics (correction)

`cell_status` is the **runner's** return code, not the agent's own verdict.

| cell | cell_status | agent-declared exit | submission |
| --- | --- | --- | --- |
| worker | error | runner exit 1 (`worker failed: timeout, inactive 146s`) | empty patch |
| mini | ok (rc 0) | **`RepeatedFormatError`** | **"" (0 chars)** |
| bid | error | `BID run failed: Worker 1 failed` (429) | empty patch |

So "mini ok" means the mini harness exited cleanly; the mini **agent** did not
succeed and never submitted. Earlier wording that described mini as a completion
was wrong.

## Termination classification

| cell | agent s | setup s | grader s | accepted / rejected | logical tokens (prompt + completion) | terminal cause | class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| worker | 146.13 | 4.31 | 198.13 | 4 / 0 | 45,766 (29,382 + 16,384) | BID inactivity 146s | model/protocol semantic |
| mini | 27.70 | 6.03 | 196.37 | 12 / 0 | 65,267 (62,337 + 2,930) | `RepeatedFormatError` | model protocol semantic |
| bid | 442.52 | 4.46 | 195.36 | 15 / 1 | 173,376 (128,129 + 45,247) | token-budget preflight 429 | budget cap after semantic burn |

All three are inside the 600 s wall; none is an infrastructure failure. Totals
across the three cells: 219,848 prompt + 64,561 completion = 284,409 logical
tokens, 196,375 cached, 589.9 s of grading.

### worker - inactivity, not wall, not budget

- 4/4 completions hit the 4096 output cap; prompts 1161 -> 5284 -> 9407 -> 13530.
- All 8 command events are `REPLACE_UNTERMINATED`: the model opened a `REPLACE`
  block but the reply ended (cap) before `---REPLACE_WITH---` / `END REPLACE`, so
  each command is a no-op (duration 0.0) and the workspace never changes.
- `git status` in the cell workspace shows only BID control docs; zero tracked
  source edits.
- `inactivity_timeout = 120` (frozen `bid_config`); the observer clock resets only
  on workspace file changes, so the cell was stopped at 146 s with 45,766 of its
  180,000 token budget used.

### bid - shared budget cap after the same protocol burn

- 1 manager request (170 completion tokens) then 14 accepted worker requests plus
  1 rejected; no reviewer or reconcile events.
- 11 of the 15 worker replies hit the 4096 cap; 22 `REPLACE_UNTERMINATED`
  commands, 6 `READ`, 2 worker inactivity timeouts (160 s) with 2 respawns and 2
  rollbacks.
- Final 429 is the proxy's own preflight: `paired-condition token budget
  exhausted before preflight`. Used 173,376, remaining 6,624; the next request
  estimated 17,977 prompt + 4,096 reserve. The rejected request has
  `usage_known: false` (never reached the model), so it adds no tokens.

### mini - `RepeatedFormatError` is ordinary model behaviour, not tool serialization

- The request carries a real `tools` array (`bash`, JSON-schema `command`).
- 8 of the 12 API responses returned proper native `tool_calls`
  (`finish_reason: tool_calls` in 8, `stop` in 4; function name `bash`, JSON
  arguments, ids). Tool serialization through llama-server works end to end.
- Turns 8, 10, 11 and 12 returned `finish_reason: stop` with **no** `tool_calls`
  and plain prose ("I have successfully updated ..."). mini's policy then raised
  "No tool calls found in the response" and after four repeats exited
  `RepeatedFormatError`.
- The last executed command (turn 7) was a `sed -i` that returned 0 while matching
  nothing; the model then reported success repeatedly.
- Conclusion: ordinary model protocol failure (native-call format abandoned), not
  a client/parser or chat-template bug. No infrastructure block.

## False completion

Artifact-supported claim, not a harness completion:

- mini's prose asserted "the `sed` command has successfully updated the
  `URLValidator` class" / "has been successfully applied", while the workspace is
  clean and the extracted patch is empty. The agent never issued the formal
  submit command, so `exit_status` is `RepeatedFormatError`, not a completion.
- worker and bid made no completion claim; both ended in an error.

No arm produced a formal completion, and none of this is inferred from the
metric-blind official grade.

## Official grades

All three: submitted 1, completed 1, resolved 1, unresolved 0, infra 0, error 0,
ambiguous 0, patch applied. All three agent patches are empty
(`sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`), and
each prediction is the controller's `.bid-empty-patch` sentinel
(prediction file sha256 recorded in `audit.json`).

`django__django-10097` is the predeclared metric-blind case, so `resolved 1/1`
here cannot establish that the bug was fixed. The grader's
`empty_patch_instances: 0` counts the sentinel-bearing prediction, not agent work.

## Server stop - the wrapper message is a second-stop artifact

| source | record |
| --- | --- |
| `runs/server-stopped.json` (pilot `finally`) | `{"pid": 18002, "reason": "SIGTERM stopped process", "stopped": true}` |
| `runtime/canary-2026-11-02/result.json` (outer wrapper) | `{"stopped": false, "reason": "executable mismatch"}` |
| `runtime/canary-2026-11-02/server.log` | `all slots are idle` ... `cleaning up before exit...` |
| now | pid 18002 gone; no llama-server, proxy or pilot process; no containers; GPU 391 MiB baseline |

The pilot stopped the owned server before exiting; the wrapper's later stop pass
saw no live cmdline and reported `executable mismatch`. The server is not
leaking. The remainder launcher reports the pilot record plus the child's
returncode instead of repeating that wording.

## Go / no-go for the 57

**GO**, with two reporting/design caveats the primary already controls:

1. `inactivity_timeout = 120` is frozen pilot config and resets only on workspace
   file changes. A cell that reads/analyses for >120 s without writing is
   terminated (the worker arm died this way at 146 s). This is not a bug to fix
   inside the cohort, but it will truncate some read-heavy attempts.
2. `cell_status` is not an agent verdict (mini). Reports must quote the agent
   `exit_status` / `cell_error` alongside it.

Everything else is in order: 3/3 graded, 0 infra/error, empty agent patches,
predictions and hashes preserved, no residual processes.
