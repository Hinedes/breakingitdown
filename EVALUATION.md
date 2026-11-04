# BID Evaluation Record

This file is the permanent, versioned evaluation record for BID. It contains
every empirical result obtained so far, including negative ones. It exists so
that the project's claims never outrun its evidence. Do not delete or rewrite
past entries; append new entries with the date, BID revision, and exact
conditions.

Evaluations are n=1 paired case studies unless stated otherwise. They are not
benchmark scores. Claims are limited to the frozen task, model, and harness
revision named in each entry.

---

## Eval 0 — Real Smoke 0 (2026-08-02, BID `ab08cba`)

**Purpose:** first real-model smoke of the restored Manager-owned
reconciliation protocol. Mechanism demonstration, not a comparison.

**Conditions:** `bid-qwen3.5:4b-ctx64k` (digest `c727d703…`, ctx 65536,
GPU-resident), llama.cpp backend via ollama, text protocol
(`BID_TEXT_TOOLS=0`), `BID_PROVISIONAL_BATCH=4`, `BID_MAX_TOKENS=16384`.
Fresh workspace, task: build `greet.py` + `test_greet.py` + `README.md`
(a 3-task plan).

**Result:** the model operated the full loop end to end in 123 s, 24 model
requests:

```
Manager PLAN (3 tasks)
→ Worker T1 WRITE greet.py, Reviewer ACCEPT → provisional ([-])
→ Worker T2 WRITE test_greet.py, Reviewer ACCEPT → provisional
→ Worker T3 (recovered from wandering via RUN ls), Reviewer ACCEPT → provisional
→ batch boundary (no unchecked remains) → Manager reconciliation
→ Manager: {project: COMPLETE, done: [1,2,3], rework: null, add: []} (1 call, 0 retries)
→ harness ratified ×3, [x] ×3
```

VC log records confirmed `provisional: task=Tn … review=ACCEPT` then
`ratified: task=Tn …` for all three tasks. The restored invariant held under
a real model: Task Reviewer ACCEPT produced `[-]`, never `[x]`; only the
Manager's decision produced `[x]`.

**Exposed defect (semantic false positive):** `test_greet.py` re-declares
`greet` instead of importing it and uses a module-level `assert` instead of a
`test_*` function — **pytest collects 0 tests**. The Task Reviewer ACCEPTed it
and the Manager ratified it. The harness enforced the protocol perfectly
while being unable to judge content quality. This is the documented quality
ceiling: the harness makes the loop honest, not the artifact correct.

**Evidence:** `evidence/real-smoke-0-ab08cba-20260802/` (55 files, hashed).

---

## Eval 1 — Slice 0, BID-BENCH-002 (2026-08-02, BID `ab08cba`)

**Purpose:** first frozen paired run. **Invalidated as a pair** (below).

**Task:** ARGUS `PhysicallyInvalidError` fail-closed contract
(BID-BENCH-002, revision 2). Repository base ARGUS at `ae587c9`. External
evaluator: `benchmark-v2/hidden_oracle.py`, pass rule = oracle checks 1–6 all
PASS (check 7 excluded as a documented evaluator defect). Model
`bid-qwen3.5:4b-ctx64k`, payload identical for both conditions (max_tokens
32768, temperature 0.6, top_p 0.95, top_k 20, min_p 0.0, repetition 1.0,
thinking enabled).

**Result:** the BID condition was **terminated by PM decision after 2 h 50 m**
because the Task Reviewer REWORK loop had **no enforced attempt bound**: 6
REWORKs on T1, 4 respawns, 1 557 model requests, **31.19 M tokens**, zero
provisional submissions, zero `[x]`. The run was testing an unbounded
`Worker → Reviewer REWORK → rollback → retry` loop, not Manager
reconciliation. Oracle on the terminal state: FAIL (4 FAIL / 2 PASS, one
PASS being the excluded check).

**Direct was not run** — the pair was not valid because the BID condition
lacked a properly enforced termination boundary.

**Outcome recorded:** `BID terminated: Task Reviewer REWORK loop had no
enforced attempt bound.`

**Demonstrated defect:** Task Reviewer REWORK retries were unbounded. Respawn
limits covered stalls/timeouts only.

**Evidence:** `evidence/benchmark-slice-0/bid-terminated/` (748 files,
hashed), outcome sha `ef15b210…` (record) / `3e2ed13a…` (slice-1 record).

---

## Eval 2 — Slice 1, BID-BENCH-002 (2026-08-02, BID `ab08cba-r2`)

**Purpose:** re-frozen paired run after adding the harness-owned REWORK
bound (`BID_MAX_TASK_REWORKS=3`). Same frozen task, base, model, payload,
ceilings as slice 0; fresh byte-identical workspaces; BID first, then Direct.

**BID condition:** terminated by the harness at the bound after **17 min**:
T1 exceeded the Task Reviewer REWORK limit (4 > 3). 531 requests,
**4.79 M tokens**, 1 respawn, zero tasks done, zero provisionals. The 4th
candidate was genuinely broken (raised `UnboundLocalError` on the all-invalid
case; returned a tuple on valid). The reviewer correctly rejected all 4 with
specific contract reasons; the last rejected candidate (`s5`) was preserved
as a VC state; `rework_limit:` was recorded in the VC log. Oracle: FAIL
(checks 1–6). This is the honest bounded outcome, recorded as such.

**Direct condition:** stalled after **70 s** — repeated-action stall (5
identical `READ argus/__init__.py`), the frozen stall rule. Zero file
changes. 291.8 K tokens. Oracle: FAIL (identical profile to the untouched
base).

**Pair verdict — Direct fail / BID fail → no demonstrated benefit.**

- Neither condition produced the contract; BID's oracle result equals
  Direct's equals the untouched base's.
- BID consumed **16.4× the tokens** of Direct (4.79 M vs 0.29 M) for the
  same failed outcome.
- There is no evidence that BID increased Qwen's coding capability on this
  frozen task.
- The one demonstrated harness improvement: slice 0's unbounded failure
  (2 h 50 m, 31.2 M tokens, 6 reworks) became slice 1's bounded failure
  (17 min, 4.79 M tokens, 4 reworks, terminal error). Bounded failure is an
  architectural property, not a capability claim.

**Evidence:** `evidence/benchmark-slice-1/bid-terminated/` (637 files) and
`evidence/benchmark-slice-1/direct-terminated/` (106 files), both hashed.

---

## Merge rationale (v0.2.0-rc1, commit `2b4a950`)

The r2 branch was merged into `main` for **architectural correctness and
bounded failure — not benchmark superiority**:

- Manager-owned reconciliation is the missing mechanism that makes BID
  actually "Breaking It Down": a Worker submission remains provisional until
  a returning Manager reconciliation converts it to DONE.
- The REWORK limit fixes a directly observed catastrophic defect (unbounded
  retry loop).
- Reverting to `main` (c18924b) would restore the one-shot Manager and the
  known unbounded retry path.

The negative ARGUS pair remains the evaluation result. No capability claim is
made on the basis of any entry in this file.

---

### Eval 4 — Difficulty ladder (2026-08-02/03, v0.2.0-rc1, commit e1b8b58)

Three tasks between stats_tools (Eval 3) and ARGUS (Evals 1-2), each with a
frozen deterministic oracle. Identical Direct/BID conditions per task,
alternating order. Model bid-qwen3.5:4b-ctx64k (c727d703...), payload
identical for both conditions.

| Level | Task | BID | Direct | Cell |
|---|---|---|---|---|
| L1 | text_stats.py (word_count/char_frequency/is_palindrome) | FAIL 6/8 (REWORK bound, 106 s) | PASS 8/8 (20 s) | Direct pass / BID fail -> regression on this task |
| L2 | roman.py (to_roman/from_roman) | PASS 33/33 (REWORK bound, 148 s) | FAIL 0/0 (empty workspace, 11 s) | Direct fail / BID pass -> harness gain |
| L3 | loan.py (monthly_payment/amortization) | FAIL 1/5 (REWORK bound, 123 s) | FAIL 6/7 (6 s) | Direct fail / BID fail -> no benefit |

**L2 anomaly (semantic false negative):** BID's terminal workspace satisfied
the evaluator 33/33 while BID itself reported failure (bid-exit-1): a later
task hit the REWORK bound, so BID never reached COMPLETE even though the
produced artifact was fully correct. The evaluator measures the artifact;
BID's status measures the loop. They can disagree in both directions.

**L1 observation:** BID failed because the model produced a genuinely
defective char_frequency (uppercase letters and spaces counted, no
lowercasing) and the Reviewer correctly rejected it 4x until the bound
fired. Direct succeeded in one session on the same task. This is the first
observed Direct pass / BID fail cell; BID's added retry overhead did not
help here.

**L3 observation:** the Reviewer's rejection reason was exactly right — the
model divided annual_rate by an extra 100 despite the task defining it as a
fraction. The oracle confirms (all non-zero-rate payments off by 100x).

**Evidence:** `evidence/ladder/runs/` (all 6 conditions, per-condition
oracle outputs + VC states + events), summary sha 6c0eec6b...

**Net reading:** one harness gain (L2), one regression (L1), one no-benefit
(L3). n=1 per cell. No capability claim; the gain is bounded failure and
occasionally a correct artifact that the loop then failed to ratify.

### Eval 4b — Ladder reproducibility check (2026-08-03, v0.2.0-rc1)

Re-ran L1 and L2 (both conditions) under the identical frozen assets to test
whether the Eval 4 gain/regression cells reproduce.

| Level | Condition | Eval 4 | Eval 4b | Reproduces? |
|---|---|---|---|---|
| L1 | BID | FAIL 6/8 (bound) | FAIL 7/8 (done) | outcome yes, path no |
| L1 | Direct | PASS 8/8 | FAIL 7/8 | no |
| L2 | Direct | FAIL 0/0 (empty ws) | PASS 33/33 | no |
| L2 | BID | PASS 33/33 (bound) | PASS 33/33 (bound) | artifact yes, advantage no |

Reading:
- The L2 BID artifact gain reproduces (33/33 twice), but it is not a
  BID-vs-Direct advantage: Direct also produced a perfect roman.py on rerun.
  The original "Direct wrote nothing" was sampling noise (an empty-workspace
  Done), not a harness limitation.
- The L1 Direct-pass/BID-fail regression does not reproduce: both conditions
  failed 7/8 on different single bugs (BID: char_frequency case handling;
  Direct: is_palindrome non-letter stripping). At temperature 0.6 a 4B model
  flips single-bug outcomes run to run.
- The one effect that reproduces: BID's terminal artifact is correct even
  when BID reports failure (REWORK bound on a later task) - the semantic
  false-negative pattern, observed in both L2 runs.

Evidence: `evidence/ladder/repro/runs/` (4 conditions), summary sha
838f5bc0..., SHA256SUMS present. n=2 per cell now; still no capability claim.

### Methodology correction — short-task evals do not test BID's purpose (2026-08-03)

The evaluation runs on stats_tools, the ladder L1-L3, and the mechanism
demo were all SHORT tasks: single-file, 5-20 line functions that a 4B model
completes in one Direct session (L2: Direct PASS 33/33 in 61 s). BID's
mechanism — decomposition, externalized state, snapshots, rollback,
provisional->Manager reconciliation — exists for ONE purpose: work whose
horizon exceeds a single session's context and reliability. On short tasks
BID is pure overhead by construction, so the "16.4x overhead / no benefit"
reading of those runs was measuring the wrong axis and does not bear on
BID's purpose. The difficulty ladder graded algorithmic complexity
(roman > word_count), but the axis that matters is HORIZON: number of
dependent steps, accumulated state, cross-file interface consistency, error
compounding across stages.

Corrective conclusion: the short-task results stand only as
reliability/bounded-failure observations. The question "does BID enable work
Direct cannot finish" is untested and requires a task that physically cannot
fit one session: multi-file, multi-stage, state-dependent, where each step
depends on the exact interface of the previous step. Eval 5 is that test.

### Eval 5 — Long-horizon pipeline (2026-08-04, v0.2.0-rc1, commit e1fe1dd)

First test of BID's actual purpose: a task that cannot fit in one 4B
session. 5-module import chain (schema -> loader -> filters -> report ->
pipeline) + sample_data.csv, end-to-end contract, deterministic oracle
(16 checks). Each module is within the model's reach individually; the
aggregate requires cross-file interface consistency across stages.

| Condition | Termination | Oracle | Wall |
|---|---|---|---|
| BID | bid-exit-1 (REWORK bound on T5) | FAIL 5/16 | 576 s |
| Direct | done (no files written) | FAIL 0/16 | 5.7 s |

BID: T1-T4 ratified in dependency order (chained VC bases s1->s5->s7->s9->s11)
with correct per-module interfaces; T5 (pipeline integration) drifted: the
worker rewrote earlier modules with dict-based interfaces and wrong CSV
columns, the Reviewer rejected with precise contract reasons across 4
REWORKs until the bound fired. The pipeline broke at the integration stage
- the expected long-horizon failure point.

Direct: emitted Done in 5.7 s with 1,185 total tokens and zero files - the
single session treated the multi-stage task as beyond its reach.

Reading: both fail, so the decision cell is Direct fail / BID fail -> no
demonstrated benefit. But the failure mode differs in the direction BID's
design predicts: BID made real progress (4/5 modules ratified, correct
interfaces) before the integration drift exceeded the REWORK bound, while
Direct produced nothing. This is consistent with BID as a horizon extender
whose remaining defect is the same-task REWORK bound firing on integration
rewrites of already-ratified files. n=1; no capability claim.

Evidence: evidence/longhorizon/runs/ (181 files hashed), summary sha
138dda20...

## Demonstration (mechanism only, no capability claim)

Any future "successful loop" run is labeled as a mechanism demonstration:
it shows the protocol operating end to end on a deliberately calibrated task
that the model can realistically finish, ending in an external evaluator
PASS. It is not compared against Direct and is not used to claim capability
improvement. The negative ARGUS pair remains the evaluation result.

### Eval 3 — Calibrated mechanism demonstration (2026-08-02, v0.2.0-rc1)

**Label:** mechanism demonstration. Not a benchmark, not compared with
Direct, not a capability claim. The negative ARGUS pair (Eval 1/2) remains
the evaluation result.

**Task (calibrated, externally verified):** create `stats_tools.py` in the
workspace root with `mean`, `median`, `stddev` (population) using only the
Python standard library. Task text sha `4621b2f0…`; evaluator sha
`7926d08c…` (deterministic: imports + exact values for `[1,2,3,4]`).

**Result: PASS (4/4 evaluator checks), BID status `done`.**

Full loop operated live with the local 4B model:

```
Manager PLAN (5 tasks)
→ T1..T4 Workers → TaskReview ACCEPT → provisional ([-]) records
→ batch boundary → Manager reconciliation → CONTINUE, ratified T1..T4
→ T5 (verification task) → provisional → Manager COMPLETE
→ Task completed!, all [x], evaluator PASS
```

VC log shows all 5 `provisional:` records paired with `ratified:` records;
Manager decisions recorded (`CONTINUE done=[1,2,3,4]`, `COMPLETE done=[5]`).
One live REWORK was correctly handled: T5 was rejected once for "no file
changes" (a verification-only task), corrected, accepted.

**Honest observations from calibration attempts (not part of the pass
rule):** the model left stray exploratory files (`mean.py`, `functions.py`)
in earlier attempts, and a "verify by running a command" final task was
rejected by the Reviewer because it produced no file changes. These are
model-hygiene and task-design observations, recorded for calibration, not
hidden.

**Evidence:** `evidence/demo-calibrated/final/` (123 files, hashed), outcome
sha `ab79b7a1…`.

---

## Reproducibility correction (2026-11-01)

The historical evidence directories, exact evaluator/oracle files, and Direct
runner source remain external to this checkout. The recorded “cannot fit one
4B session” phrasing is not supported by measured prompt/context saturation
in the available record; Direct's zero-file `Done` only establishes early
termination without an artifact. Its implementation is unverified and must
not be described as a native coding agent or representative baseline without
the runner identity and source. No historical result or comparator claim is
independently verified by the following fixture.

`evaluation/repro/` contains a new frozen smoke task, oracle, and paired-run
metadata protocol. It is a reproducibility example, not a replacement for the
missing ARGUS or long-horizon task artifacts, and it makes no architecture
claim. A future comparison must archive both runner implementations, exact
launch commands/prompts/tools, model payload and digest, total budgets,
stopping rules, raw traces, workspace/output hashes, oracle, and all repeated
runs. If those identities or budgets differ, report the pair as unmatched.

**Later historical replay:** the Eval 5 long-horizon archive was hash-checked
and its BID/Direct final workspaces were re-evaluated in the rootless sandbox.
The replay reproduces BID 5 pass/6 fail groups and Direct 0 pass/1 fail group.
See `evaluation/studies/2026-11-01-llama1b/HISTORICAL_REPLAY_REPORT.md`.
This validates that named pair only, not all historical runs. The separate
benchmark-v2 task matches its manifest, but its oracle sidecar mismatch and
missing final workspaces still block replay.

---

## Eval 6: Llama 3.2 1B fixed-task study (2026-11-01/02, BID tree based on `1399767`)

**Purpose:** bounded evaluation of BID role/batch/context conditions against a
custom single-Worker Direct protocol. This Direct comparator is not an as-is
coding agent. This new study's results do not replace or generalize the
separate, bounded Eval 5 historical replay.

**Conditions and schedule:** immutable source epoch 9; five conditions
(B128-B4, B128-B1, B64-B4, D128, D64), three frozen synthetic tasks
(easy word statistics, multi-file sales pipeline, repository range bug), and
three repeats per task/condition. D128 is a shared paired reference for the
two 128K BID conditions; D64 is shared for B64-B4. All 45 queued outcomes
completed before the hard cutoff. Effective contexts were 131072 and 65536,
one slot, on RTX 5070 Ti. Pinned F16 Llama 3.2 1B GGUF SHA-256
`1f33ad43d2b85b908ff06fe7002b69806a57359b9b2617ca27d7bdea428ae146`,
llama.cpp v0.8.0 commit `8642541b255bfb5dc39cd351a178be09013a55d6`.
Temperature 0.6, top-p 0.95, top-k 20, min-p 0, repetition penalty 1.0,
thinking enabled. Per cell: 300 s, 50,000 accounted prompt+completion tokens,
8192 output tokens/request. Prompt tokens were preflighted; missing usage and
overshoots were zero. Worker continuity reset and `BID_REPO_CONTEXT=0`.

| Condition | Easy: pass; partial checks | Pipeline: pass; partial checks | Repo fix: pass; partial checks |
|---|---:|---:|---:|
| B128-B4 | 0/3; 0/12 | 0/3; 3/21 | 0/3; 6/9 |
| B128-B1 | 0/3; 0/12 | 0/3; 3/21 | 0/3; 6/9 |
| B64-B4 | 0/3; 0/12 | 0/3; 3/21 | 0/3; 6/9 |
| D128 (shared) | 0/3; 0/12 | 0/3; 3/21 | 0/3; 6/9 |
| D64 (shared) | 0/3; 0/12 | 0/3; 3/21 | 0/3; 6/9 |

**Result:** zero binary passes in 45 cells; 45 of 210 individual oracle
checks passed. All 27 paired task/repeat comparisons tied on binary outcome,
with zero BID-only and zero Direct-only wins. These fixed-seed pairs are not
independent Direct runs and do not support significance claims. Terminations:
9 harness errors after the BID Manager failed to produce valid TODO in three
attempts on every easy cell; 30 token-cap prompt stops; 6 aggregate token-cap
stops. No wall caps, missing usage, or token overshoots. Workers attempted 708
RUN commands, all denied; no generated code ran outside the isolated oracle
sandbox. Thirty-six Worker sessions produced no Task Reviewer verdict or
Manager reconciliation decision.

Every cell's final task-artifact tree matched its seed: zero changed or added
task files. The 45 partial checks came from already-present seed artifacts, not
new model work. Across the 15 task-condition groups there were 25 distinct
response-sequence hashes (17 distinct sequences across the full matrix); the
same fixed server seed `20261101` was reused for every repeat. These repeats
are not independent replications. Maximum preflight prompt length was 17,837
tokens, below both allocated contexts; 64K versus 128K was not meaningfully
exercised. The recorded BID batch-1/4 condition was not reached by a task
batch, so that contrast is not an effective ablation either.

**Interpretation:** treat epoch 9 as a protocol floor and method confound, not
an architectural negative. All nine BID easy cells failed Manager TODO
parsing, so none of the 45 cells exercised the complete Reviewer and Manager
reconciliation loop. The custom Direct baseline did not require that planning
path, and is not an as-is coding agent. This epoch establishes that this
unaltered 1B setup did not complete the frozen tasks under the measured
protocol; it does not compare architectures or establish that BID fails with
other models. Initial setup smokes preceded fixture/oracle freeze; they used
no task artifacts and remain separate from these outcomes.

The epoch-9 runner's manifest lookup resolves to the immutable epoch directory
in the archived source layout, and all 45 outcomes record the expected
`68143ceb5f63886a99c5f57f2b6c7300cd79f5dbdf056d8a8dcf06bf5a324f1f` hash.
The exact manifest is preserved locally as
`evaluation/studies/2026-11-01-llama1b/SOURCE_MANIFEST.epoch-9.json`.

**Evidence:** full request traces, per-cell outcomes, and queue are preserved
remotely under `/home/iclab/bid-studies/2026-11-01-llama1b/` (`epoch-9/`,
`runs-epoch-9/`, `queue-epoch-9.sqlite3`, `summary.json`). Portable report and
frozen plan: `evaluation/studies/2026-11-01-llama1b/EXPERIMENT_REPORT.md` and
`PLAN.md`. Final run summary SHA-256
`4ebbbe96f5854bc6d8923b8b49b56c55f2da3f04ae35358193508c35f0f5bf30`.

### Post-epoch-9 strict-protocol calibration

Prompt and validation changes were frozen in distinct exploratory source epochs
10 through 12; none modifies the epoch-9 artifacts. The 1B probe failed during
Manager/Worker protocol use. A separate same-family 3B Q8_0 cohort also failed
to reach Reviewer or Manager reconciliation in two probes. Epoch 12 briefly
wrote the requested contents but omitted the required newline, then repeated
an invalid `END READ` command until the unsubmitted change was reset. All
outcomes, traces, command logs, and per-epoch manifests are under
`evaluation/studies/2026-11-01-llama1b/calibration-evidence/` and documented in
`CALIBRATION_REPORT.md`. These are protocol-floor results, not architecture
comparisons. No batch/context matrix or execution-enabled condition was run;
RUN remains denied because the current oracle sandbox does not persist Worker
edits; bounded isolation checks passed, but no persistent transfer mechanism
was qualified. See `RUN_SANDBOX_ASSESSMENT.md`. A separate runner-only epoch 13 adds external POSIX process-group
supervision, atomic outcome/checkpoint writes, setup-cost accounting, explicit
per-repeat seeds, and separate cached-token reporting. It ran no model cells;
its immutable manifest is `SOURCE_MANIFEST.epoch-13.json`.

---

## Overnight closeout — safety stop and cutoff (2026-11-02)

The experiment window closed at 08:00 Taipei. Epoch 18 was not resumed: the
safety-stopped cohort is recorded as 17 completed outcomes, one interrupted
outcome, and 27 pending. Those are partial exploratory observations only;
repeated seeds across epochs are not independent replications and must not be
pooled. Epoch 20 was planned but not launched. No new comparative cohort
completed, and no native coding-agent baseline was run.

The immutable epoch-20 source candidate is identified by manifest SHA-256
`e925ca9e3d3e6c3c6e9cb6f9be19e04aa9f171bffd74ba4e6299d0e12d20c051` (36
source/fixture files). Recorded broad suite results were Ubuntu 197 passed and
Windows 175 passed / 10 capability-specific skips. A separate focused check
was independently run against the immutable source with
`test-venv-epoch-10/bin/python -m pytest -q -p no:cacheprovider
tests/test_symlink_boundaries.py <epoch20/source/evaluation/studies/2026-11-01-llama1b/test_study.py>
-k 'symlink or named_pipe or worker_executor or artifact_hashes'`: **9 passed,
9 deselected in 1.48s**. It covered Linux symlink and FIFO boundaries, the
persistent Worker executor, session-detaching timeout cleanup, and artifact
hashing. These are focused implementation checks, not study outcomes or a
comprehensive kernel/security guarantee.

Boundary correction: host-side copies, snapshots/restores, observers, review
readers, and artifact hashing must preserve symlinks as links and avoid opening
special files; symbolic control roots are rejected before initialization.
Trusted namespace PID 1 supervises model commands as child processes so a
session-detaching descendant remains subject to timeout cleanup. These changes
address observed boundary/timeout defects within tested paths; they do not
establish general hostile-filesystem race safety, kernel escape resistance, or
aggregate cgroup resource enforcement.

Primary independently verified final runtime state at 09:52 Taipei: no study
processes remained; owned llama-server PID 8611 matched the expected binary,
3B Q8_0 model, and localhost:8081 endpoint, then was stopped gracefully with
SIGTERM. No llama-server remained. GPU state afterward was 391 MiB used and
15,605 MiB free of 16,303 MiB. The primary-created record is
`evaluation/studies/2026-11-01-llama1b/FINAL_RUNTIME_STATE.json`.

The delegated evidence-export attempt did not complete in its execution
environment; its cause is not established here. The primary successfully used
SSH and remote Ubuntu-22.04 to verify the epoch-20 manifest and focused Linux
checks, and independently verified runtime shutdown. Remote SAFETY_STOP, run
summaries, and calibration `outcome.json` files were not exported into this
checkout, and the raw epoch-18 queue was not recomputed. The 17/1/27 counts
remain recorded from the closeout instruction and local plan, not newly
verified here. Do not treat this entry as a raw-evidence export. Existing local
epoch-9–13 source manifests and calibration outcomes remain archived in the
study folder. Closeout details and this limitation are recorded in
`evaluation/studies/2026-11-01-llama1b/FINAL_CLOSEOUT.md`.

---

## Development smoke — Worker stall regression (2026-11-02)

This bounded product-development run is separate from all historical epochs and
is not a benchmark result. A real Llama 3.2 3B Q8_0 run using the ordinary BID
prompts and text parser exposed repeated multi-command turns that evaded stall
detection. The fix counts repeated no-progress command sequences and does not
count no-op writes as progress. The deterministic lifecycle regression passes,
but the real model still failed to follow the text protocol: after the patched
Worker was marked stalled, subsequent malformed REPLACE output reached the
smoke token ceiling before Reviewer or Manager reconciliation. No completion or
capability claim is made. The exact task, launch/runtime configuration, both
smoke outcomes, manifest and trace hashes, and limits are recorded in
`evaluation/development-smoke/REPORT.md`. Existing epoch sources, queues, and
historical traces were not edited.

## Independent corrected-source Qwen verification (2026-11-02)

Primary independently ran the ordinary CLI against a separate corrected-source
snapshot, manifest SHA-256
`f4e87c40701c449c9b1f6afc35fb7aa99502e2839882257d204a8b6264e72c2a`. Qwen
3-4B Q8_0 completed a two-file arithmetic task through Manager, Worker, Reviewer,
and reconciliation; both tasks were checked and ratified. `init` and `run` both
exited 0. Existing isolated `run_python` checks returned `[-4, 0, 10]` for
`double(-2, 0, 5)` and ran `main.py` with exit 0 and stdout `42\n`. Linux suite:
195 passed in 2.83s. Batch was 1; grammar and host RUN were off. The owned
server stopped in `finally`; post-stop GPU use was 390 MiB, with 15,606 MiB
free. This is a bounded end-to-end result, not a general reliability or
architecture claim.
Full config and artifacts are in
`evaluation/development-smoke/primary-verification-2026-11-02/`; prior Llama
and Qwen run records remain unchanged.

## v1 SWE-bench Verified pilot and rule-following probes (2026-11-03/04)

The 60-cell paired pilot (BID, Worker-only, mini-SWE-agent; 20 instances; Qwen3-4B-Instruct-2507 Q8_0;
frozen source `7112ff6f3c57…`) completed with no infrastructure failures. Each arm resolved 1 of 20
instances, the same already-passing `django__django-10097` in all three, so the pilot sits at the floor
and does not rank the arms. BID's runner ended in `error` on all 20 cells: 11 token-budget exhaustions
after unbounded `file not found` loops, 6 Manager plans rejected by the strict plan parser (a fenced code
snippet under an item), and 3 reconciliation decisions rejected after a task escalation. The pilot BID
config had left repository navigation (`MAP`/`FIND`) off, so the BID and Worker-only arms could only guess
paths: 43% and 41% of their Worker requests followed a `file not found`. Their comparison with each other
and with mini is therefore not a fair architecture measure. Per-cell data, cause table and the full
analysis: `evaluation/swebench-pilot/results/v1-2026-11-03/{RESULTS.md,cells.json}`.

Fixes were made in the source and each has a regression test that fails without it: repository navigation
on in the pilot config, a plan parser that folds fenced code into its task, a missing-file hint with a
bounded stall, reading `# Rework` on an escalated task as a rewrite, and a bound on consecutive turns that
change nothing and learn nothing. The fixed source was **not** re-run through the 60-cell pilot.

`bid/rules.py` now lists each Manager/Worker rule with its enforcement level, and
`evaluation/rule_probes/` measures the prompt-only rules on the real 4B model (same server recipe as the
pilot, fixed per-trial seeds; graders proven on known-good and known-bad samples in `test_probes.py`).
Raw results: `evaluation/rule_probes/results/2026-11-04/`.

| Probe (rule) | Result |
|---|---|
| `manager_names_target_file`, 20 real SWE-bench issue statements | 19/20 plans name a target file for every item; 19/20 valid on the first attempt |
| `worker_one_command`, 20 trials on a small fixture repo (before the no-progress bound) | 17/20 trials fully single-command; 1,716 of 1,936 responses (88.6%); target file read in 15/20; no trial had more than 2 missing files in a row; 7 trials ran 192–279 responses |
| `worker_one_command`, same trials on the final source | 15/20 trials fully single-command; 380 of 390 responses (97.4%); target file read in 14/20; every trial ended within 12–25 responses |

The one long trial read in detail (trial 7, 279 responses) was a cycle of three different errors (`REPLACE`
without a delimiter, `REPLACE` with empty old text, `READ … END READ`), which the previous-turn stall check
cannot see; the other six were not read. Their response counts repeated across reruns, so they look
deterministic per seed, and all seven are gone under the no-progress bound.
Limits: one small synthetic repository, one model, one run per seed, 20 trials per probe; trial-level
"single-command" counts a trial as failed if any response held more than one command. Prompt-only
"exactly one command" is violated in about 1 response in 40 and remains a candidate for harness
enforcement. Six of the 20 final-source trials did not read the target file; the probe does not yet say
why. These are rule-adherence measurements, not a capability or end-to-end reliability result.
