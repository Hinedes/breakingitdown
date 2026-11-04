# Django first-issue preflight failure

Date: 2026-11-02. Purpose: official base/gold validity gate for
`django__django-10097` before any model start. No model server was started and
no scored pilot cell was run.

## Runs

- Gold: `preflight-gold-django10097-9abb9561`
- Base sentinel: `preflight-base-django10097-9abb9561`
- Dataset: frozen `frozen/grading/instances.jsonl`, instance only.

## Aggregate results

| run | submitted | completed | resolved | unresolved | infra | error | empty patch |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gold | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| base | 1 | 1 | **1** | 0 | 0 | 0 | 0 |

Both reports: `patch_successfully_applied=true`; FAIL_TO_PASS success 438,
failure 0; PASS_TO_PASS success 1432, failure 0. The frozen P2P count is 1432
(task repo says 1427), so the frozen row was used.

The base test output ends with `>>>>> Test Exit Code: 1`, yet the official
report classified all 438 FAIL_TO_PASS as success and resolved the base. The
base sentinel patch only adds `.bid-empty-patch`; it cannot fix URL validation.
This is an unexpected base pass for the frozen row/image pair.

## Decision

STOP. Per primary instruction, no test counts were changed, no replacement
issue was chosen, no cherry-pick, no grading-rule change, and no model was
started. Full hidden reports, logs, and patches remain only in remote
`logs/evaluation/<run_id>/`; this local directory contains aggregate
`results.json` only.

Required: primary decision on the Django image/dataset compatibility or a new
epoch. The remaining 57 cells are not launched.
