# v1 SWE-bench Verified pilot — results (2026-11-03/04)

Frozen source tree `7112ff6f3c57…`, sealed manifest `manifest-7112ff6f3c57.json`, 20 gold/base-gated
instances × 3 arms = 60 cells, Qwen3-4B-Instruct-2507 Q8_0 on llama.cpp (`n_ctx` 65536, one slot),
600 s wall and 180k logical tokens per cell. All 60 cells completed and were graded by the official
SWE-bench grader (`grade_returncode` 0 everywhere, zero infrastructure failures). Per-cell data:
[`cells.json`](cells.json).

## Outcome

| Arm | Cells | Resolved | Runner `ok` | Runner `error` |
|---|--:|--:|--:|--:|
| BID | 20 | 1 | 0 | 20 |
| Worker-only | 20 | 1 | 11 | 9 |
| mini-SWE-agent | 20 | 1 | 13 | 7 |

The single resolved instance is `django__django-10097` in all three arms. It already passes at the base
commit (a metric-blind instance), so its "resolve" comes from an empty patch and is not evidence of
capability. Every other instance is unresolved in every arm: **this pilot is at the floor for all three
arms and cannot rank them.**

## Why BID ended in `error` on every cell

| Cause | BID cells | What happened |
|---|--:|---|
| Token budget exhausted (HTTP 429) | 11 | The Worker looped on `file not found` until the 180k logical-token budget ended the cell |
| Manager plan rejected | 6 | The Manager answered with a sensible plan that included a fenced code snippet; the plan parser accepts only bare `- [ ]` lines, so all three attempts failed |
| Reconciliation rejected | 3 | After a task escalation the Manager answered `# Rework T1` for the escalated (unchecked) task; the validator accepts `# Rework` only for provisional tasks |

Root causes, none of them a model-capability limit:

1. **The pilot's BID config never enabled repository navigation.** `runner.bid_config` left
   `repo_context_mode` unset (default off), so the Worker had no `MAP`/`FIND` and could only guess paths in
   a repository it had never seen. In the BID arm 193 of 451 Worker requests (43%) followed a
   `file not found`; in the Worker-only arm, which shares that config, 134 of 327 (41%). The loop was
   unbounded because each guess has a different path and so never matched the repeat-action guard.
   Reproduced offline: 76 requests, the same count as the pilot cell that ended on the budget.
2. **The plan parser rejected any non-item line**, including a code fence under an item, while the Manager
   prompt asked for literal content to be copied verbatim.
3. **The escalation prompt was prompt-only.** It told the Manager to use `# Replace Remaining Plan`; the 4B
   Manager used the word the rest of the protocol teaches it, `# Rework`.

## What this invalidates

The BID and Worker-only arms were run without repository navigation, so their comparison with each other
and with mini is not a fair measure of either architecture. mini has a shell and was not affected. The
earlier 23 graded cells (source `c63701b9…`) are not comparable with this run.

## Fixes (this commit)

Each fix has a regression test that fails without it; see `tests/test_v1_fixes.py`.

- `runner.bid_config` sets `repo_context_mode: tools`.
- `ManagerInitAdapter._todo` folds a fenced block or indented line into the item above it; other stray
  lines still reject the plan.
- A second consecutive missing-file READ returns a hint toward `FIND`/`MAP`; all missing-file READs share
  one signature, so a run of guesses is a bounded stall.
- A `# Rework` naming the escalated task is read as a rewrite of that task (its text plus the stated
  requirement), with other unchecked tasks carried over unchanged.
- Consecutive Worker turns that change no file and learn nothing end in a soft reset and then a stall
  (`2 × repeat_action_limit`; `FIND` turns excluded, they have their own budget). Found afterwards by the
  rule probes: a three-error cycle that the previous-turn repeat check cannot see.

The fixed source has **not** been re-run through the 60-cell pilot, so no pilot-level claim is made about it.
Rule-following evidence on the real model is in `evaluation/rule_probes/` (see `EVALUATION.md`).
