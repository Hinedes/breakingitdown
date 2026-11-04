# Pause diagnosis: `django__django-11133--mini`

Read-only controller diagnosis. No source, prompt, manifest or checkpoint change;
no model start, no rerun, no rescore, no filtering of the recorded patch.

## Verdict

The cohort paused on `grader completed_instances=0`, and that pause rule fired
correctly on what the grader returned (`error_instances=1`). The cause is a
**controller patch-export flaw**: the frozen extractor force-adds gitignored
runtime bytecode, so the candidate patch is unappliable.

This is **not** an image/dependency fault, cache contamination, a test-patch
conflict, or benchmark test tampering. It is also not "a fix that failed to
apply" - the candidate made no source fix at all.

## Exact chain

1. The mini agent ran Python inside its cell container, which mounts the host
   workspace at `/testbed`. That wrote **145 `django/**/__pycache__/*.pyc`**
   files (cpython-36) into the checkout, plus one scratch file
   `test_memoryview.py`.
2. `runner.extract_patch_in_container` (frozen `bid-source-f4999e9656a5`)
   runs:

   ```
   git add -N -f -- . ':(exclude)<BID control paths>'
   git diff --binary <base_commit> -- .
   ```

   `-f` overrides `.gitignore`, so the 145 `.pyc` files became "intent to add"
   and entered the diff. `protocol.filter_patch` only removes BID control
   paths, not `__pycache__`.
3. The extracted patch has **146 `diff --git` headers: 145 `.pyc`, 1 real**
   (`test_memoryview.py` at repo root; no file under `tests/`).
4. The official grader applies the patch inside the image's `/testbed`, where
   those same `.pyc` files already exist from image build
   (`/testbed/django/__pycache__/__init__.cpython-36.pyc` is present).
5. `git apply --verbose` (and `--3way`, `--reject`, then `patch -p1`) all fail:

   ```
   The next patch would create the file django/__pycache__/__init__.cpython-36.pyc,
   which already exists!  Skipping patch.
   ```

   `run_evaluation` logs `>>>>> Patch Apply Failed` and raises `EvaluationError`.
6. `results.json` therefore records: submitted 1, **completed 0**, resolved 0,
   unresolved 0, **error 1**, infra 0. The wrapper's rule
   `completed_instances == 1` paused the cohort.

Evidence: the workspace shows the `.pyc` entries as `A ...` (staged) while
`.gitignore` contains `__pycache__` - only the extractor's `-f` can produce that;
the frozen source has exactly one `git add` (runner.py:326).

## Why the canary did not catch it

All three canary patches were empty, so the extractor never had a tree to
export. Any cell whose agent imports Python inside the repo will hit this,
including Worker and BID, because they use the same extractor.

## Cell facts

| field | value |
| --- | --- |
| checkpoint | present, `completed=true`, grade rc 0 |
| agent exit | mini `info.exit_status = RepeatedFormatError`, submission `""` |
| agent patch | non-empty, sha256 `1304f4ef…664b44`, 812,291 chars |
| prediction file | sha256 `b4c2cfb8…df495b` |
| patch content | 146 headers: 145 `__pycache__/*.pyc`, 1 `test_memoryview.py` |
| benchmark tests touched | no |
| official | submitted 1, completed 0, resolved 0, unresolved 0, error 1, infra 0 |
| usage | 16 requests, 63,630 prompt + 3,314 completion = 66,944 logical, 59,046 cached |
| max | prompt 5,821, completion 348, requested cap 4,096 |
| timing | setup 7.08 s, agent 29.69 s, total 55.3 s (grade + remainder 18.53 s) |
| budget | 600 s wall, 180,000 tokens |

The agent's own exit was `RepeatedFormatError` and its submission was empty, so
even with a clean extractor this candidate had no source fix. That does not
change the classification: the official outcome here is an **error**, not
"unresolved".

## Remediation (prospective, primary review required)

`runner.py` / `protocol.py` are inside the `f4999e…` source fingerprint, so any
fix needs a **new versioned source identity and manifest pin**, not an in-place
edit:

1. Stop forcing ignored paths into the patch: `git add -N` without `-f`
   (respecting `.gitignore`), with an explicit allow-list only if some
   non-ignored-but-excluded file truly must be included; and/or
2. drop `__pycache__/`, `*.pyc`, `*.pyo` in extraction/`filter_patch`.

Do not filter this checkpoint's patch or rescore it. Keep the checkpoint,
prediction, `grade.log` and `results.json` byte-identical. No model retry for
this cell.

## Runtime

`final.json`: ran 1, skipped 3, checkpoints 4, canary hashes unchanged,
`server_stop` = `{stopped: true, pid: 23533, returncode: 0, wrapper_escalated:
false, alive_after_stop: false}`. The primary's fresh check reported no LLM,
proxy, pilot or container left. This file does not re-derive live state.

Machine-readable copy: `pause-diagnosis.json`.
