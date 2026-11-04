# Epoch 12 3B literal-content calibration

This is one final protocol probe after epoch 11. The Manager produced a
well-formed but incomplete plan, and the Worker invented file contents, so the
prompt now explicitly requires copying literal task content verbatim and not
splitting it into steps. This remains an exploratory prompt treatment, not an
architecture comparison.

The unseen single-file task is `calibration-epoch-12-3b/TASK.md`. The hidden
check runs only after the role loop ends and checks exact `tag.txt` bytes plus
no additional task files. Require Manager initialization, Worker write,
Reviewer verdict, Manager reconciliation, and the hidden check to all pass.

Use only the epoch-11 3B cohort model/runtime: pinned Q8_0 weights, Q8_0 KV,
65,536 context, one slot. Generation seed is `20261104` for every model
generation and token preflight. Budget remains 30,000 accounted tokens,
8,192 output tokens/request, and 300 seconds wall time, supervised by an
external process-group timeout. RUN and SEARCH are unavailable. Preserve this
source, task, run, and complete trace as epoch 12; do not change epochs 9-11.

Stop after this probe regardless of outcome. If full role coverage still
fails, report that execution-enabled and architecture-comparison conditions
were not exercised; do not create another tuning epoch or batch matrix.
