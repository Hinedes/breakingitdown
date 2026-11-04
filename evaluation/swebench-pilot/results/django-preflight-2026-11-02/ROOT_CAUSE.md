# Root cause: `django__django-10097` base resolves under official v5 grading

Date: 2026-11-02. Controller-only inspection; no model was started and no
scored cell was run. Companion to `PREFLIGHT_FAILURE.md`.

## Verdict

This is **not** an evaluator/image/dataset compatibility fault. The image is
correct: the base commit fails the tests the test patch creates and the gold
patch makes them pass. The defect is in the official expected-test metadata -
the six tests the fix actually targets are missing from the official
`FAIL_TO_PASS` list, so official grading has nothing left to discriminate on.

`django__django-10097` is therefore an **official-metric already-resolved,
non-discriminating case**: an empty base patch is scored `resolved` because the
declared expected tests all pass at base. This is **not** a claim that the base
code is correct - the six real regression tests still FAIL at base and only the
gold patch makes them pass. Official `resolved` on this case cannot establish
that the underlying issue was fixed.

It is also not guaranteed that every arm gets identical credit here: a candidate
patch can still regress any of the 1432 `PASS_TO_PASS` tests, which would
unresolve it. Keep the case in the manifest, do not replace it, and do not patch
the evaluator.

## Exact chain

1. `eval_script` runs the whole Django suite:
   `./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1`.
2. The test patch appends six URLs to `tests/validators/invalid_urls.txt` and
   rewrites one line of `tests/validators/valid_urls.txt`.
3. `tests/validators/tests.py` builds `TEST_DATA` from those two files at import
   time (lines 303-306) and names each generated case
   `test_%s_raises_error_%d` from the enumeration index (lines 309-311). The six
   appended URLs therefore create `test_URLValidator_raises_error_352` ...
   `357` - names that did not exist before the patch.
4. At the base commit the old regex accepts them, so all six FAIL. With the gold
   patch they PASS:
   - base run: `FAILED (failures=51, errors=83, skipped=884, ...)`
   - gold run: `FAILED (failures=45, errors=83, skipped=884, ...)`
   - the delta of 6 is exactly `test_URLValidator_raises_error_352..357`.
5. The official `FAIL_TO_PASS` list has 438 names and contains none of them (and
   no `validators.tests` name for these cases at all). All 438 pass at base. The
   1432 `PASS_TO_PASS` tests also all pass at base.
6. Grading therefore resolves the base sentinel:
   - `get_logs_eval` (`swebench/harness/grading.py:113`) only rejects a log whose
     status map contains *no* FAILED/ERROR while the recorded exit code is
     non-zero (lines 166-175). Base has 51 FAILED + 83 ERROR, so the guard does
     not fire.
   - `get_eval_tests_report` (line 179) scores only the names in
     `FAIL_TO_PASS`/`PASS_TO_PASS`. Unlisted failures are ignored.
   - `get_resolution_status` -> `f2p == 1 and p2p == 1` -> `ResolvedStatus.FULL`.

Exit code 1 is genuinely unrelated to the declared lists: the remaining 45
failures and 83 errors are the *same set* in the base and gold logs (set
difference is exactly the six URL tests), and they are environment-flavoured
whole-suite failures (`sites_tests`, `fixtures`, `admin_scripts`,
`user_commands`, ...). The official protocol is right to ignore them - the wrong
part is the expected list, not the exit-code policy.

## Ruled out

| Hypothesis | Evidence |
| --- | --- |
| Image pre-applied the gold patch | `django/core/validators.py` still has `(?:\S+(?::\S*)?@)?`; tree diff vs `b9cf764` is zero; HEAD is the synthetic build commit `88d9340` |
| Gold run leaked into the base run | Separate run IDs/containers; base still fails the six URL tests while gold passes them |
| Parser collapsed FAIL into PASS | The six appear as `FAIL` and are recorded `FAILED` in the parsed status map (`test_grading_coverage.py`) |
| Multi-phase log overwrote results | Single test phase between `>>>>> Start Test Output` / `>>>>> End Test Output` |
| Wrong working directory / mount | `eval_script` cds to `/testbed`; log paths and `git checkout` operate on `/testbed` |
| Sentinel patch side effects | Sentinel only adds `.bid-empty-patch`; it cannot touch URL validation |
| Frozen row differs from official data | Frozen `FAIL_TO_PASS`/`PASS_TO_PASS`/`patch`/`test_patch`/`base_commit` are identical to `princeton-nlp/SWE-bench_Verified` c104, original `SWE-bench` test split, and `swe-bench-tasks` 3d07b46 |
| A corrected official revision exists in the references inspected | Only one commit touches `tasks/django__django-10097/tests.json` in `SWE-bench/swe-bench-tasks` (`34d7c5e`, 2026-08-15 restructure); it carries the same 438. `SWE-bench/SWE-bench_Verified` 78f471 has the same `FAIL_TO_PASS` and 1427 `PASS_TO_PASS` (the 5 extra frozen P2P entries are `generic_inline_admin` tests that pass at base). This is not an exhaustive proof over all versions ever published |

## Regression

`evaluation/swebench-pilot/test_grading_coverage.py` runs the pinned evaluator
against a synthetic Django-format log: the parser sees the unlisted failure, the
run resolves while the failure is outside `FAIL_TO_PASS`, and it does not resolve
once that name is declared. It pins the mechanism without embedding the gold
patch or the hidden expected list.

```
SWEBENCH_SRC=<...>/source/SWE-bench python test_grading_coverage.py
OK: parser sees the failure; omission alone flips resolved to True
```

## Gate analysis plan (fixed before any model outcome)

For the all-20 gold/base gate sweep, report two separate views and keep all 20
results visible:

1. **Raw official metric**: `resolved` per issue, including cases whose metric is
   blind (empty patch resolves). No task is dropped or replaced because its gate
   is blind.
2. **Discriminator subset**: official `resolved` restricted to cases that pass the
   gate, i.e. gold officially resolves and the base sentinel does not. Only these
   can evidence that a condition did something.

A blind case is never described as "the issue was actually fixed". If any case
shows gold FAIL or an infrastructure error, pause and diagnose the cause rather
than excluding it.

## Consequence for the cohort

- The new `test_grading_coverage.py` lands inside the `evaluation/swebench-pilot/*.py`
  source fingerprint scope, so the approved source/manifest identities
  (`74416f0b...20c94` / `b2fca9af...ae48ea`) no longer describe the live tree. A
  prospective identity is recomputed after all preparation edits; the previous
  identity and the failed-gate preparation records are preserved (see
  `PREP_PROVENANCE.md`).
- No test counts were edited, no tests removed, no base forced to fail, no
  reference answer placed in the agent environment.
- The case stays in the frozen 20 with an explicit metric-blind label: an empty
  patch is officially resolved, so official `resolved` here does not establish
  the issue was fixed, and a candidate can still unresolve it by regressing one
  of the 1432 `PASS_TO_PASS` tests.
- The other 19 gold/base gates were observed after this analysis plan was fixed:
  all 19 are gold-PASS / base-FAIL discriminators, and the Django case is the
  only base-already-official-pass case (`../gate-2026-11-02/GATE_RESULTS.md`).
