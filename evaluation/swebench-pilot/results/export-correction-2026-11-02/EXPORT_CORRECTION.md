# Export correction: ignore-native patch extraction

Date: 2026-11-02. Scoped versioned infrastructure correction for the controller
patch extractor. No model inference, no retry, no rescore, no checkpoint rewrite.

## Scope

The extractor force-added gitignored runtime artifacts into the candidate patch,
which made the `django__django-11133--mini` patch unappliable. The fix removes
that forced inclusion at the single shared point. It does not add an extension
blacklist, does not filter by hand, and does not touch the grading path.

## Change

Two call sites, one token each:

```
evaluation/swebench-pilot/runner.py:96   extract_patch()
  -    _git(["add", "-N", "-f", "--", ".", *excludes], cwd=workspace)
  +    _git(["add", "-N", "--", ".", *excludes], cwd=workspace)

evaluation/swebench-pilot/runner.py:326  extract_patch_in_container()
  -    "... add -N -f -- "
  +    "... add -N -- "
```

Git semantics that now hold:

| case | before | after |
| --- | --- | --- |
| ignored `__pycache__` / `*.pyc` | included | excluded |
| untracked legitimate source | included | included |
| tracked edit / binary edit / deletion | included | included |
| BID control paths | excluded by pathspec | excluded by pathspec |
| ignored file the model deliberately `git add -f`ed | included | included (it is in the index, so `git diff <base>` carries it) |

No `filter_patch` change; no extension list.

## Regression

`evaluation/swebench-pilot/test_extract_patch_ignored.py` (Docker only, no model,
no network). It builds a throwaway repo with `.gitignore` (`__pycache__/`,
`*.pyc`), generated `.pyc`, an untracked `fix_new.py`, a tracked source edit, a
tracked binary change, a tracked deletion and control files, extracts with both
policies, and applies the result inside the same task image against a fresh base
tree that already contains the ignored cache.

- fails on `f4999e96…` (old): `AssertionError: ignored artifacts leaked`
- passes with the fix: `EXTRACT_REGRESSION_OK`
- also asserts the workspace files are unchanged by extraction and that the
  image/global git config is unchanged

Local suites: `evaluation/swebench-pilot` 37 passed / 5 skipped; repo `tests/`
185 passed / 10 skipped.

## The four final workspaces are recoverable without touching them

Method: read-only container, separate `GIT_INDEX_FILE` freshly `read-tree`d from
the base commit, then `git add -N` (ignore-native) and `git diff --binary
<base>`. The original `.git/index`, working tree and HEAD are never written.

| workspace | tree unchanged | index unchanged | original headers (pyc) | recovered headers (pyc) | recovered patch |
| --- | --- | --- | --- | --- | --- |
| django__django-10097--worker | yes | yes | 1 (0) | 0 (0) | empty diff (original is the sentinel) |
| django__django-10097--mini | yes | yes | 1 (0) | 0 (0) | empty diff (original is the sentinel) |
| django__django-10097--bid | yes | yes | 1 (0) | 0 (0) | empty diff (original is the sentinel) |
| django__django-11133--mini | yes | yes | 146 (145) | 1 (0) | `test_memoryview.py`, sha256 `24815c45…a6766a` |

For `django__django-11133--mini` the recovered patch sha256 equals the sha256 of
the original patch's non-`.pyc` chunk exactly (`24815c45afee152e1df2b0fa9c83a32841a249bf7c39e0e349f394ff65a6766a`),
so nothing legitimate is lost.

Index ambiguity check: all 145 `__pycache__` entries in that workspace index are
intent-to-add with the **empty blob** (`e69de29b…`), i.e. zero content, and the
mini trajectory contains **no `git add` command** (10 commands, none git). No
model-intentional ignored staging exists to preserve or lose.

## No silent pooling

The four altered cells are names individually and kept apart; the other 56 are
untouched. `django__django-11133--mini` keeps its original checkpoint,
prediction, `grade.log` and `results.json`; its official record stays an
**error** (submitted 1, completed 0, error 1), not "unresolved". Its four apply
attempts (`git apply`, `--3way`, `--reject`, `patch -p1`) remain in the log. No
model retry.

## Prospective identities (prepared, not deployed)

| item | value |
| --- | --- |
| product/prompt/pyproject hash | `e33c1cc781bab0d6f47ac2e251ff4a0617a2f820ae90568aa33a55ea4767b2b2` (**unchanged**) |
| old source / manifest | `f4999e96…817da` / `9e662f11…5f0c0` |
| new source | `c63701b9c9c623053ed387f816738b411071ef74a6a7685f1cb3ab39058817da` |
| new manifest | `b0cf7f6d2b3dfddbd9e769762d00d6569b88b31a9d0a420b87685d424d33b03d` |
| changed files | `runner.py`, new `test_extract_patch_ignored.py` (nothing else) |

The old manifest is preserved byte-exact at
`results/export-correction-2026-11-02/manifest-f4999e9656a5.json` (file sha256
`8a75e35b…ed5330`); identities in `identities.json`. The local `manifest.json`
now pins the prospective source. No remote snapshot or remote manifest was
written.

## Proposed versioned grading of the four (awaiting primary approval)

1. Recover the four patches with the corrected policy into a separate
   `predictions/export-correction-2026-11-02/` path; do not overwrite the
   original predictions.
2. Grade them with the same official dataset/image rows under **new** run IDs
   (`verified20-<condition>-c63701b9c9c6-a1-…`), keeping all 60 original records.
3. Per-cell metadata needed for consistent scoring: `key`, `instance_id`,
   `condition`, `generation_seed`, original `run_id`, original prediction sha256,
   recovered patch sha256, workspace tree hash, new prediction sha256, new
   `run_id`, and the official aggregate counts.
4. Lineage: the model inference for these four cells happened under
   `f4999e96…` / `9e662f11…`. Only post-execution export and grading change.
   Reports must say so; they must not imply all inference ran under the new hash.
5. The remaining 56 cells stay unchanged and run with the fixed extractor.

## Prefix launch record and outcomes (primary, main-owned)

The primary launched the prefix grading of the 4 derived cells in a main-owned
background shell on 2026-11-02 (job `sh_19c72acbf001axhSLivyGvWJrE`). No model
was started. Prefix completed at `2026-11-02T13:39:04Z`; the primary independently
verified the 4 official results, the unchanged original checkpoint/prediction
hashes and the source lineage. Full record: `prefix-result.json`.

| key | new grade run id | corrected official | original official |
| --- | --- | --- | --- |
| django__django-10097--worker | `verified20-worker-c63701b9c9c6-a1-0c12f35a68ae` | completed 1, resolved 1, unresolved 0, error 0, infra 0 | completed 1, resolved 1 (metric-blind) |
| django__django-10097--mini | `verified20-mini-c63701b9c9c6-a1-511cea73c8c2` | completed 1, resolved 1, unresolved 0, error 0, infra 0 | completed 1, resolved 1 (metric-blind) |
| django__django-10097--bid | `verified20-bid-c63701b9c9c6-a1-616e5f5a8eca` | completed 1, resolved 1, unresolved 0, error 0, infra 0 | completed 1, resolved 1 (metric-blind) |
| django__django-11133--mini | `verified20-mini-c63701b9c9c6-a1-685ee1a253e5` | completed 1, resolved 0, **unresolved 1**, error 0, infra 0 | **error 1**, completed 0 (retained) |

Lineage note: `django__django-11133--mini` recovered only `test_memoryview.py`
(patch sha `24815c45…a6766a`); the 3 canary cells recovered empty patches. The
derived cells' controller `status.json` is the copied original status, so its
`patch_sha256` is the **original** model patch (e.g. `1304f4ef…664b44` for
11133), not the corrected one — the exporter reports `recovered_patch_sha256`
and keeps the original status separately. The corrected grade is artifact
scoring of a saved workspace, not evidence that mini actually fixed the issue;
the 10097 cells remain officially metric-blind (empty patch resolves), so their
`resolved 1` still proves nothing about a fix. The original 11133 official
`error 1` stays listed separately and is not overwritten or excluded.

## Continuation launch record (56 cells)

The primary passed the remote preflight (4 derived / 56 missing, live state
clean, 72.17 GiB free) and launched the 56 model attempts in a main-owned
background shell (job `sh_19c7cc0040012cTvW2bjeQMx6G`) under
`runtime/remainder-v2-2026-11-02` with `runs-export-v2` as the run root. No
reruns, no model for the prefix cells. **No claim is made here about the 56
outcomes**; they are reported only after the primary's completion notification.

## Exporter (TEMP)

- `C:\Users\hind\AppData\Local\Temp\opencode\bid_swe_export.py`
- bytes / sha256: `35780` /
  `501e04a905670d8f516b9a4e132317428b0f380ec5f3fd4c397bd3299db4e0eb`
- `--self-check` -> `SELF_CHECK_OK 5 blocks` (derived row uses recovered patch +
  separate original status; missing checkpoint is pending; mini
  `RepeatedFormatError` false / `Submitted` true / unknown null; worker harness
  status faithful; failing logged usage counted, 502 and accepted-unknown flagged,
  preflight 429 not unknown; gate IDs must match the manifest and 19
  discriminators; grade error kept separate).

Commands (explicit inputs, no silent identity defaults):

```
$B/venv/bin/python $B/runtime/export-v2-prefix-2026-11-02/bid_swe_export.py \
  --runs-root $B/runs-export-v2 \
  --manifest $B/manifest-c63701b9c9c6.json \
  --source-root $B/bid-source-c63701b9c9c6 \
  --out <controller results dir>

# after the 56 continue:
$B/venv/bin/python $B/runtime/export-v2-prefix-2026-11-02/bid_swe_export.py \
  --runs-root $B/runs-export-v2 \
  --manifest $B/manifest-c63701b9c9c6.json \
  --source-root $B/bid-source-c63701b9c9c6 \
  --out <controller results dir>
```

Per-row semantics: derived cells report `recovered_patch_sha256` /
`new_prediction_sha256` with the original `status.patch_*` kept in
`original_status`; new cells report their own status patch. Lineage columns carry
inference vs export source/manifest, and new cells are labelled
`computed-from-checkpoint`. Model cost is charged once (derived rows
`model_cost_charged_here=false`); `grade_s` is the checkpoint's actual value when
present and a `tqdm-estimate` otherwise. `original_official` stays separate from
the corrected grade. Paired stats split `missing`, `both_resolved` and
`both_unresolved`; all-20 and the 19 predeclared discriminators are reported
separately with no significance or fix claim.

## Files

- `identities.json` - old/new hashes, per-file pilot hashes, changed files
- `workspace-feasibility.json` - per-workspace integrity and recovery evidence
- `manifest-f4999e9656a5.json` - byte-exact previous manifest
- `../remainder-2026-11-02/PAUSE_DIAGNOSIS.md` / `pause-diagnosis.json` -
  the original pause diagnosis

## Deployment record (2026-11-02)

| item | value |
| --- | --- |
| remote source | `/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/bid-source-c63701b9c9c6` |
| remote source tree hash | `c63701b9c9c623053ed387f816738b411071ef74a6a7685f1cb3ab39058817da` |
| remote manifest | `/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/manifest-c63701b9c9c6.json` |
| manifest self-hash | `b0cf7f6d2b3dfddbd9e769762d00d6569b88b31a9d0a420b87685d424d33b03d` |
| manifest file sha256 | `25a47ef4be468052e96bf7a1d4fbe43193910f077572ffbe2be91afcb9f9fd0c` |
| product/prompt hash old == new | `e33c1cc781bab0d6f47ac2e251ff4a0617a2f820ae90568aa33a55ea4767b2b2` |
| old source / old root manifest | `f4999e96…817da` / `b2fca9af…ae48ea` (untouched) |
| `verify_provenance` on new root | OK, 20 public rows, 20 grading rows |
| deployed regression | `EXTRACT_REGRESSION_OK` (Docker, no model) |

## Prepared entrypoints (TEMP, corrected, not launched)

| script | sha256 | runtime |
| --- | --- | --- |
| `runtime/export-v2-prefix-2026-11-02/bid_swe_prefix_v2.py` | `167b97186a741a698693ccb0072b40e91b6c6f60426d72cd64b22a85db027180` | `runtime/export-v2-prefix-2026-11-02` |
| `runtime/remainder-v2-2026-11-02/bid_swe_remainder_v2.py` | `f6a4c815aa6072377b8452f26deaa9de55b01bb69f005099506c8ef7be1b4696` | `runtime/remainder-v2-2026-11-02` |

Corrections applied after primary review:

- recovery mounts the repo itself at `/testbed:ro` with `-w /testbed` (was mounting
  the repo at `/work` and using a non-existent `/work/repo`), and redirects the
  object store to the container tmpfs (`GIT_OBJECT_DIRECTORY`,
  `GIT_ALTERNATE_OBJECT_DIRECTORIES=/testbed/.git/objects`) so `git add -N` can
  create the empty blob without writing to the read-only project mount;
- no host git at all: tree/index hashes are plain file reads and HEAD is read
  from `.git/HEAD` as a validated detached 40-hex sha; no file or parent
  symlink is followed, and the tree hash represents symlinks by target string
  instead of reading their targets (Django's base tree contains 4-12 legit
  symlinks);
- the continuation's apply-failure branch no longer returns early: a known clean
  `Patch Apply Failed` is recorded but infra counts, prediction presence,
  completion and proxy/HTTP checks still run.

`--recover-check` (read-only, no predictions/grades/checkpoints) on the actual
entrypoint recovered all four from the saved workspaces:

| key | recovered patch sha256 | chars | tree/index/HEAD unchanged |
| --- | --- | --- | --- |
| django__django-10097--worker | `e3b0c442…b855` (empty) | 0 | yes |
| django__django-10097--mini | `e3b0c442…b855` (empty) | 0 | yes |
| django__django-10097--bid | `e3b0c442…b855` (empty) | 0 | yes |
| django__django-11133--mini | `24815c45afee152e1df2b0fa9c83a32841a249bf7c39e0e349f394ff65a6766a` | 373 | yes |

Symlink-safe tree hashes used for the integrity assertion (old algorithm followed
symlink targets and is retired): worker `8e8c0b8f…ec3e`, 10097-mini
`99afd4c3…67da`, bid `d357681c…1658`, 11133-mini `0e2ff937…c930`; index/HEAD
hashes are unchanged from the feasibility run.

Self-checks: prefix `SELF_CHECK_OK prefix metadata + supervisor plan + original
snapshots`; supervisor `SELF_CHECK_OK supervisor plan + apply-failure
classification` (clean apply failure ordinary; apply failure + HTTP 502 infra;
apply failure + infra count infra; unknown error 1 infra).

- prefix output root `runs-export-v2`; the 4 derived checkpoints carry
  `inference_source_sha256=f499…`, `inference_manifest_sha256=9e66…`,
  `export_source_sha256=c637…`, `export_manifest_sha256=b0cf…`,
  `artifact_scoring=v2-prefix-reuse`.
- the continuation requires exactly those 4 checkpoints and 56 missing cells, and
  treats an official `Patch Apply Failed` with a clean (no ignored bytecode) patch
  as a candidate outcome instead of infrastructure; anything unknown still pauses.

