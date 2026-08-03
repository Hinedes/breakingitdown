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
