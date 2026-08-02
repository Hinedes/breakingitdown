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

## Demonstration (mechanism only, no capability claim)

Any future "successful loop" run is labeled as a mechanism demonstration:
it shows the protocol operating end to end on a deliberately calibrated task
that the model can realistically finish, ending in an external evaluator
PASS. It is not compared against Direct and is not used to claim capability
improvement. The negative ARGUS pair remains the evaluation result.
