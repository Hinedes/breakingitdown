# Epoch 10 strict-protocol calibration

**Purpose:** test whether the exploratory Manager/Worker prompt treatment can
complete an unseen, small file-creation task through actual BID role coverage.
This is protocol calibration only, not an architecture comparison or a
benchmark result.

**Task:** `calibration-epoch-10/TASK.md`. The hidden check compares the exact
`launch-card.txt` bytes and confirms no other task files were added. The
expected bytes and checker are not mounted in the Worker workspace. The Worker
has no RUN or SEARCH capability; RUN stays denied.

**Condition:** one fresh BID project with Manager planning, Worker edits, Task
Reviewer verdict, and Manager reconciliation. Record the number of requests
per role and whether each stage actually completed. Stop if the Manager cannot
initialize after the normal bounded retries. No task-specific retry or
response parsing beyond the normal harness is allowed.

**Model/runtime:** pinned Llama 3.2 1B F16 on the existing local llama.cpp
server, one active model only, localhost endpoint. Use generation seed
`20261102` in every generation request. Maximum 30,000 total prompt plus
completion tokens, 8,192 output tokens/request, and 300 seconds wall time.
Preflight prompt-token accounting is included. An external process-group
timeout supervises the cell; no next generation starts until the process group
has exited. Preserve the full trace and outcome in a new epoch-10 run folder.

**Acceptance:** the Manager creates a valid TODO; a Worker creates the exact
file; Reviewer emits a verdict; Manager reconciliation emits a decision; the
external hidden check passes; every outcome records the epoch-10 manifest
digest and generation seed. Missing any role is a calibration failure, not a
pass based on files alone.

**Change boundary:** this exploratory prompt/protocol treatment follows epoch
9 and does not alter its plan, source, manifest, queue, traces, outcomes, or
interpretation. No batch/context ablations or larger matrix until this
calibration demonstrates complete role coverage.
