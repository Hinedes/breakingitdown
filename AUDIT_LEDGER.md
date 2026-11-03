# BID architecture and research audit ledger

Audit started 2026-11-01. Repository commit `1399767f1a117f22becf40afade71885baf18afd` (2026-09-07, “Update README for current BID architecture”). Initial `git status --short --untracked-files=all` was clean. No cancelled-agent ledger or audit artifact existed. This file is the only audit-created/modified file; source, tests, configuration, docs, and workspace were not changed.

**Historical baseline note:** the statements above describe audit-time state, not current status. Product fixes were authorized and applied on 2026-11-01; current resolutions and validation are recorded under each finding and in the continuation section below. No commit was made.

## Scope, method, and limits

Read files: root `bid.py`, `pyproject.toml`, `.gitignore`, `README.md`, `EVALUATION.md`, `EVIDENCE.md`; all `bid/{__init__,adapter,cli,harness,model,observability,observer,permissions,search,session,todo,tools,vc}.py`; `prompts/{manager,worker}.md`; `.github/workflows/tests.yml`; all source tests `tests/{__init__,test_harness,test_permissions,test_todo,test_vc}.py`; and `workspace/docs/{task,todo,project-status,decisions}.md` plus `workspace/.bid/{current,log.md,states/s0/...}`. Also checked the tracked-file inventory, status, and source/test symbol callers. Traced init → plan → Worker command loop → snapshot → Task Reviewer → provisional admission → Manager reconciliation/recovery, plus tools/search/telemetry and rollback callers. The research goal is to compare the same underlying edge model organized as Workers/Managers against coding agents as-is, separating model identity from architecture/baseline effects; it is not merely to confirm that Manager and Worker share a backend.

No live model/search/evaluation call or worker against the project workspace was used. Read-only SSH/location probes and authorized Python/dependency setup did use external access; no model or search service was contacted. No standalone evidence directories, frozen benchmark inputs/oracles, dependency lockfile, or CI artifacts are present in this checkout; historical evidence paths point to `/home/iclab/bid-eval/...`. There are no other CI workflows or config files in the checked-in inventory. Existing tracked `tests/__pycache__` bytecode, including stale `test_session...pyc`, was preserved. Runtime checks below were enabled by installing per-user Python 3.12.10 and dependencies only into `C:\Users\hind\AppData\Local\Temp\opencode`; no packages/build artifacts were installed in the repository. The earlier blocked-check result is retained in the change log.

### Status vocabulary

- **Confirmed/static**: directly established by current source/docs or deterministic flow tracing; not necessarily dynamically reproduced.
- **Confirmed/reproduced**: the stated behavior was exercised with a deterministic or fault-injected check in an isolated temporary workspace; this does not imply every production trigger or consequence was reproduced.
- **Confirmed/conditional**: reproduced only when the stated precondition is injected, such as an intervening external edit.
- **Historical/reported**: stated in the checked-in evaluation record, but underlying external artifacts were unavailable for independent verification.
- **Hypothesis/unverified**: plausible code path or interpretation not established by available evidence; retain as an open question rather than a confirmed finding.

## Subsystem register

| ID | Purpose, entrypoints, inputs/outputs, connections | Implementation/documentation and state |
|---|---|---|
| S1 CLI/config/package | `bid.py:2-5` → `bid/cli.py`; `harness.get_config` at `bid/harness.py`. Env and argv → workspace init/run/status/VC actions. | Explicit `bid`/`prompts` packages, prompt assets, `pyproject.toml` version, and installed `bid` console script verified in isolated editable and wheel installs (F8, F15). |
| S2 Manager planning | `harness.init_project:334-387` → `ManagerInitAdapter.run` `adapter.py:424-508`; reads task + `prompts/manager.md`, writes TODO. | Implemented. Manager creates file-producing checklist as prompt/docs require, but list sequencing/task IDs are assigned by harness, not model. Existing checklist has little content validation beyond line syntax/non-empty tasks. |
| S3 Worker executor | `run_project` `harness.py` → `run_worker_session` → `WorkerAdapter.run`; inputs task/feedback/policy/model output; outputs workspace edits, RUN evidence, candidate snapshot. | Worker prompt and both recovery surfaces list the supported protocol. Worker remaining time caps model and RUN timeouts; custom blocking backends remain cooperative only (F2, F7). |
| S4 Model/provider transport | `create_backend` → `LlamaCppBackend.run`; OpenAI-compatible HTTP endpoint/model/settings → assistant content and telemetry usage. | Shared backend remains. Worker passes no native function definitions and parses its own text commands. All roles use the configured endpoint/model; it can be remote and has no role-specific resource policy. |
| S5 Role tools / protocol | Worker command parser/dispatch is `adapter.py`; production uses the text protocol. | Unused native `tools.py`/`session.py` transport and inert `BID_TEXT_TOOLS` toggle removed. |
| S6 Permissions / isolation | `permissions.py`, `_safe_path`, Worker READ/WRITE, RUN and protected-control snapshot in `adapter.py`. | Application path checks and protected-control rollback remain. RUN is denied by default; `BID_ALLOW_HOST_RUN=1` opts into arbitrary argv with host-user privileges and network access. No OS/container/process sandbox; direct deletion checks and selected protected roots are not containment (F1). |
| S7 Workspace/Git lifecycle | `VersionControl` in `vc.py`; called by init, candidates, rollback, reconcile and CLI. Snapshot state excludes `.bid`; not Git operations. | Manual rollback remains the warned destructive history-pruning operation; normal rework uses non-pruning workspace restore. Init refuses an existing `.bid_backup`; OS file locks release at process exit, while the lock file itself remains (F3, F4, F14). |
| S8 Task protocol, recovery, retries | TODO parser and provisional/reconciliation state machine in `todo.py` and `harness.py`. | Durable reconciliation intent replays idempotent ratification/rework/TODO effects; Task Reviewer and Manager rework counters persist independently across restarts (F5, F6, F12). |
| S9 Search/research evidence | `search.py`; called by Worker and configured in `harness.py`. | Provider/cache/evidence-file path remains bounded. SEARCH is documented in the Worker protocol; unused citation helper removed, and citations are not a blanket requirement. |
| S10 Evaluation / baseline / scoring | Record `EVALUATION.md`, index `EVIDENCE.md`; evaluation tooling is separate from BID's production loop. | Added frozen task/oracle fixtures, rootless execution checks, a resumable queue, token/wall-budget accounting, and per-cell traces under `evaluation/studies/2026-11-01-llama1b/`. Epoch 9 completed 45 cells but is a protocol floor/method confound: all BID easy planners failed, no Reviewer/reconciliation coverage or task-file progress, and batch/context settings were not exercised. Follow-up 1B and 3B calibrations also failed to reach full role coverage; their exact prompts, traces, and manifests are preserved in `CALIBRATION_REPORT.md`. No architecture matrix or execution-enabled RUN condition was run. The two named rehearsal archives and the Eval 5 long-horizon run archive were hash-verified; Eval 5 BID/Direct oracle outcomes reproduced in the sandbox. Broader historical validation and benchmark-v2 replay remain open (F9). |
| S11 Telemetry / evidence | `observability.py:1-66` used throughout adapters/harness; `observer.py:1-44` used to detect workspace changes; run evidence stored in VC candidate descriptions. | Event log is sibling `<workspace>-events/events.jsonl`, while VC log is in `.bid/log.md`; model prompts do not receive events. Telemetry records per-request usage/timing but no in-repo rollup/evaluation output. Observer activity uses filesystem hashes and is separate from model evidence. |
| S12 Tests / CI | `tests/test_harness.py`, `test_permissions.py`, `test_todo.py`, `test_vc.py`; `.github/workflows/tests.yml`. | Windows symlink cases skip only on specific unsupported-platform errors; direct `rm`/`rmdir`/`unlink` allow-list paths are exercised with mocked subprocess argv. Full local suite and CI platform status recorded below. |
| S13 Workspace example | `workspace/docs/{task,todo,project-status,decisions}.md`, `.bid/current`, `.bid/log.md`, `.bid/states/s0`. | Checked-in starter workspace states “Design a note-search app” but has no task lines; current `s0`, initialization snapshot. It is representative starter state rather than a complete runnable BID plan; `run` will return `no TODO tasks` (static from parser + runner). |
| S14 Q12 inference runtime | `/home/iclab/bid-llama-runtime/`, `/home/iclab/bid-models/`; llama.cpp OpenAI-compatible endpoint on WSL localhost 8081. | RTX 5070 Ti verified. One resident model and one slot; roles do not reload weights. 1B F16 and separate 3B Q8_0 cohorts are not pooled. 3B at 131072 context/Q8 KV used 12,194 MiB and left 3,802 MiB free in the phase-18 check. Allocated context is not demonstrated reliable recall. |
| S15 Experimental driver/provenance | `evaluation/studies/2026-11-01-llama1b/study.py`, immutable remote `epoch-N/source` and manifests. | Separate generation token and external process-group wall budgets; explicit per-repeat seeds; all role calls and cached tokens recorded. Epoch 9 is a fixed-seed protocol-failure cohort. The phase-18 constrained/sandboxed cohort runs separately, not as a replacement. |
| S16 Experimental execution/oracles | Study `sandbox.py`, `oracles.py`, `protocol.py`, `test_study.py`; generated artifacts and hidden independent checks. | Oracle copies remain disposable. A tested optional Worker executor persists only its writable project through a private bind mount, not copy-back. Default host RUN is still denied. Constraints are an explicitly exploratory protocol treatment. Native-agent parity, comprehensive kernel containment, and aggregate cgroup limits are not claimed. |

## Connection map (implemented flow)

`bid.py` or installed `bid` entry point → CLI/env config → init writes task/policies and starts VC → Manager planner → todo parser → shared model backend. `run` → provisional log/TODO reconciliation → Worker text protocol (READ/WRITE/REPLACE/RUN/SEARCH/DONE) → workspace mutations and RUN/search evidence → candidate snapshot → Task Reviewer (saved base-to-candidate diff; explicitly says no harness verification) → non-pruning fixed-base rework or ACCEPT provisional admission → Manager reconciliation intent/replay → ratify/rework/add/replace/complete. VC snapshots/log and TODO are the task state; sibling JSONL events are observability. Search cache is under `.bid/search_cache` and search docs under `docs/research`.

Disconnected or absent links: Manager “verification” is a model decision using supplied diffs/evidence, not an independent evaluator; no historical baseline runner or process isolation. A separate frozen smoke example/oracle is not an architecture benchmark. Manager and Worker role prompts change instructions, not underlying model/provider or resource policy. Sharing the same model is an input-control fact, not evidence that the architecture beats a coding agent baseline.

## Findings

**Historical source citations:** line references in the finding Evidence sections below describe baseline commit `1399767f1a117f22becf40afade71885baf18afd`; they are not current-file line numbers.

### F1 — RUN is arbitrary host-privileged execution, not a sandbox

- **Severity/certainty:** High / Confirmed-static (the security impact depends on treating model/task/workspace as untrusted).
- **Subsystems:** S3, S6, S7.
- **Evidence:** `adapter.py:954-985` parses with `shlex.split` then invokes `subprocess.run(argv, cwd=workspace, ...)` with no shell but also no OS restrictions; `adapter.py:967-972` only validates direct deletion commands; protected state list is finite at `adapter.py:63-72`; prompt permits `RUN` (`prompts/worker.md:9-10,29-30`). README accurately says argv/no shell and documents protected roots (`README.md:149-166`), but “path checks/isolation” should not be conflated with execution containment.
- **Root cause and trigger/consequence:** Worker can run an interpreter or any available executable, which can write outside workspace, make network calls, alter user files, spawn children, or bypass direct-delete parsing. E.g. a Python `-c` command can call filesystem/network APIs. The protected-state snapshot only covers listed workspace roots and is a post-command restore, not containment. A model-generated or task-induced command can therefore damage host resources or exfiltrate credentials as the harness user.
- **Smallest repair direction (not applied):** Treat RUN as trusted-code execution and document/require an explicit trust boundary, or move it into an OS sandbox with filesystem/network/process limits; do not rely on command token filtering.
- **Validation:** Static call-chain confirmed. No host-side command payload was run; containment concern does not require a destructive proof.
- **Resolution (2026-11-01):** RUN is denied unless `BID_ALLOW_HOST_RUN=1`; the central executor and direct inner entry both enforce the gate. README usage describes host-user privileges and the absence of a sandbox. **Status:** fixed as an explicit trust boundary, not contained execution. **Validation:** `test_run_is_denied_by_default_without_calling_subprocess`; no host command was run.

### F2 — Worker protocol instructions and recovery feedback drift from implemented commands

- **Severity/certainty:** Medium / Confirmed-static.
- **Subsystems:** S3, S9.
- **Evidence:** README lists `SEARCH <query>` and describes provider/cache behavior (`README.md:62-87`). Parser dispatch exists `adapter.py:256-260`; executor branch calls `execute_search` `adapter.py:659-686`. `prompts/worker.md:7-27` lists READ/RUN/REPLACE/WRITE/Done but omits SEARCH. The no-command retry at `adapter.py:608-610` omits both SEARCH and REPLACE; unknown-command feedback at `adapter.py:835-840` tells the Worker only READ/WRITE/RUN/Done. The policy is loaded directly by `WorkerAdapter.run` at `adapter.py:536-550`.
- **Root cause and trigger/consequence:** Protocol knowledge is duplicated instead of shared. The Worker may omit available search, may not recover from an empty turn with REPLACE, and can be told a valid REPLACE or SEARCH command is disallowed after another unrecognized command. Thus current behavior depends on which prompt/feedback surface was seen.
- **Smallest repair direction:** Define/derive every prompt and recovery command list from one protocol source, or align all literal command lists and tests.
- **Validation:** Static caller trace confirms parser/dispatch support and prompt-feedback omissions. No provider/network call.
- **Resolution (2026-11-01):** Added SEARCH to the worker prompt and reused the same command list for both recovery messages. **Status:** fixed. **Validation:** `test_analysis_only_response_gets_command_only_retry` and protocol tests in `tests/test_harness.py`.

### F3 — `init` can delete a pre-existing sibling `.bid_backup`

- **Severity/certainty:** High / Confirmed/reproduced in an isolated temporary workspace.
- **Subsystems:** S1, S7.
- **Evidence:** `harness.py:334-344`: backup path is fixed at `os.path.join(parent, ".bid_backup")`; if it exists it is recursively removed before current workspace is renamed there.
- **Root cause and trigger/consequence:** Unowned/unvalidated path reuse. Running `bid init` on an existing workspace when the sibling path already contains data silently destroys that data. Because the path is sibling to workspace, it can be user-owned unrelated content.
- **Smallest repair direction:** Use a unique temporary backup or fail closed when the path exists; never recursively erase a pre-existing path not created by this operation.
- **Validation:** Reproduced safely in a temporary directory: existing sibling `.bid_backup/user-data.txt` disappeared after successful init. No project workspace was used.
- **Resolution (2026-11-01):** Init refuses to proceed when the fixed sibling backup path already exists, including a dangling symlink; it never removes unowned backup data. **Status:** fixed. **Validation:** `test_existing_backup_is_preserved_and_init_fails_closed`.

### F4 — User rollback contradicts append-only VC-log promise

- **Severity/certainty:** Medium / Confirmed-static.
- **Subsystems:** S7, S8.
- **Evidence:** README calls the VC log append-only and authoritative (`README.md:135-145`). CLI exposes `vc rollback` at `cli.py:67-72`, calling `VersionControl.restore`; `vc.py:124-129` calls `_truncate_log(state_name)` and deletes all later snapshot directories. `_truncate_log` overwrites the existing log (`vc.py:248-263`).
- **Root cause and trigger/consequence:** Rollback uses destructive history rewrite rather than an append-only rollback event. A user may believe audit history is permanent; rollback removes later records/snapshots, including provenance/recovery evidence.
- **Smallest repair direction:** Choose one contract: append a rollback event while retaining immutable history/snapshots, or revise “append-only/authoritative” claims and explicitly warn that rollback prunes history.
- **Validation:** Static CLI-to-VC trace; no rollback executed.
- **Resolution (2026-11-01):** Kept manual `vc rollback` destructive, added a CLI warning, and documented its history-pruning contract. Normal task rework restores workspace contents without pruning snapshots or VC events. **Status:** fixed with an explicitly destructive manual repair path. **Validation:** `test_restore_rolls_back`, `test_restore_workspace_keeps_append_only_history`.

### F5 — Crash during ratification can leave a permanently inconsistent TODO/log pair

- **Severity/certainty:** High / Confirmed by fault-injected interruption at the ratification write boundary.
- **Subsystems:** S7, S8, S11.
- **Evidence:** Admission is explicitly log-first at `harness.py:809-817`; `_provisional_records` treats later `ratified` as superseding provisional at `harness.py:189-199`. Reconciliation writes the ratified record before writing TODO (`harness.py:580-599`). Resolver repairs only active provisional + unchecked; provisional TODO without active record fails closed (`harness.py:208-250`).
- **Root cause and trigger/consequence:** Ratification has no matching recoverable transaction protocol. If process stops after append of `ratified:` and before TODO write, TODO remains `[-]`, but record is no longer active; next `run` returns “inconsistent persisted state: Tn is provisional without a valid provisional record” rather than completing or repairing. Similar partial states are possible while applying multiple tasks.
- **Smallest repair direction:** Make reconciliation transitions replayable/idempotent from durable decision records or add explicit recovery for ratified-log/TODO mismatch; keep filesystem writes and event/log updates in a defined transaction protocol.
- **Validation:** Reproduced in a temporary workspace by injecting an exception on the TODO write after the `ratified:` append; `_resolve_provisional_state` then returned the inconsistent-state error. Existing suite has admission interruption coverage but not ratification interruption coverage.
- **Resolution (2026-11-01):** Manager decisions are atomically journaled before side effects, then replay ratification, rework restore/invalidation, add/replace TODO edits, and Manager state idempotently on restart. Manager REWORK feedback is recovered by task and base state from the append-only log, including after replay, and included in the resumed Worker prompt without exposing other tasks' reasons. **Status:** fixed for journaled reconciliation effects and task-specific feedback recovery. **Validation:** existing reconciliation replay tests and `test_manager_rework_reason_reaches_resumed_worker` (successful and interrupted/replayed paths).

### F6 — Per-task REWORK bound resets on restart

- **Severity/certainty:** High / Confirmed/reproduced across two isolated `run_project` invocations.
- **Subsystems:** S8, S10, S11.
- **Evidence:** `rework_counts = {}` is local to each `_run_project_inner` invocation (`harness.py:675-679`); only this dict drives configured limit at `harness.py:760-794`. Resume invokes a fresh `run_project`; persistent `rework_reason` only carries latest reason (`harness.py:800-802`, recovery helpers `106-131`), not count. README says max Task Reviewer reworks per task is configurable (`README.md:176-180,198`).
- **Root cause and trigger/consequence:** Safety limit is volatile while project state is durable. Repeatedly restarting after each rejected candidate resets count; a task that should terminate after limit can loop indefinitely across restarts and consume unbounded requests/tokens. Historical Eval 1 found this class of unbounded loop; current in-process cap does not establish restart-safe boundedness.
- **Smallest repair direction:** Persist monotonic per-task review attempts/rework count in authoritative VC events and derive/enforce the bound during resume.
- **Validation:** Reproduced in temporary workspaces with a deterministic always-REWORK reviewer and `max_task_reworks=1`: two consecutive `run_project` calls each allowed two Task Reviewer REWORKs before stopping, recording two `count=2 limit=1` events.
- **Resolution (2026-11-01):** Task Reviewer REWORK events persist monotonically in VC history and restore the per-task count on each run; terminal exhaustion cannot be bypassed by restarting, including legacy terminal records. **Status:** fixed. **Validation:** `test_rework_limit_terminates_after_nth_rework`, `test_legacy_terminal_rework_record_stays_terminal`.

### F7 — Worker timeout is cooperative, not a hard wall-clock deadline

- **Severity/certainty:** Medium / Confirmed/reproduced with a fake monotonic clock and backend.
- **Subsystems:** S3, S4, S6.
- **Evidence:** loop condition checked at `adapter.py:563`, then blocking `backend.run` at `565-569`; hard ceiling checked again only after model response. RUN has separate timeout at `adapter.py:974-983`, which may be configured longer than Worker timeout. Defaults are `worker_timeout=3600`, request timeout=300, RUN timeout=60 (`harness.py:21-26`).
- **Root cause and trigger/consequence:** No remaining-deadline calculation/cancellation across nested calls. A request begun before the ceiling may return after it; a RUN may also exceed it if `BID_RUN_TIMEOUT` is set larger. The Worker can pass the deadline while blocking calls or the current turn's commands finish.
- **Smallest repair direction:** Pass remaining session deadline to model and RUN timeout/cancellation, or describe the control as between-call ceiling rather than strict wall time.
- **Validation:** Reproduced with a fake monotonic clock and fake backend: a response advancing simulated time by 5 seconds returned `Done` despite `worker_timeout=1`.
- **Resolution (2026-11-01):** The misleading hard-ceiling guarantee is removed. The Worker checks its cooperative deadline before model dispatch, after model response, before accepting `Done`, and immediately before RUN dispatch; built-in HTTP phase and RUN timeouts are capped by the remaining budget. Blocking work can still overrun until it returns, and HTTP timeouts are phase limits, not hard cancellation. **Status:** original hard-guarantee contradiction resolved as a cooperative deadline, not a hard wall-clock guarantee. **Validation:** fake-clock tests cover slow model response, `Done` followed by a slow command, and RUN preflight time spent snapshotting; `tests/test_harness.py`.

### F8 — Package version metadata disagrees

- **Severity/certainty:** Low / Confirmed-static.
- **Subsystems:** S1.
- **Evidence:** `pyproject.toml:1-3` is version `0.2.0-rc1`; `bid/__init__.py:1` exports `0.1.0`.
- **Root cause and trigger/consequence:** Version was not updated in both metadata locations; runtime/version reporting can disagree with installed distribution and complicates evaluation identity/reproducibility.
- **Smallest repair direction:** Use one source of truth or synchronize the two values.
- **Validation:** Static read.
- **Resolution (2026-11-01):** The project version uses normalized PEP 440 spelling (`0.2.0rc1`) in metadata and the source-checkout fallback, matching installed distribution metadata. **Status:** fixed. **Validation:** isolated wheel/editable metadata and installed import checks below.

### F9 — Historical evaluation is informative but not independently reproducible from this checkout

- **Severity/certainty:** Medium / Confirmed-static about availability; empirical claims are historical/reported.
- **Subsystems:** S10, S11.
- **Evidence:** `EVIDENCE.md:3-10` says evidence exists only on evaluation machine under `/home/iclab/bid-eval/evidence`; evaluation entries reference external data (`EVALUATION.md:51,84-85,122-123,175-176,262-263`); no such evidence/oracle files in tracked inventory. EVAL itself states n=1 and no capability claim (`EVALUATION.md:9-11,140-141,178-180,254-260`).
- **Root cause and trigger/consequence:** Results are prose/index records, not self-contained reproducible assets. This audit cannot verify hashes, task/oracle implementation, model payload equality, Direct runner details, or reported results. EVAL calls the five-module task “physically cannot fit one session” / “cannot fit in one 4B session” (`EVALUATION.md:225-237`) but provides no measured token/context saturation evidence. It says Direct “treated” the task as beyond reach because it emitted Done with zero files (`:251-252`); the output establishes early termination/no artifact, not the model's reason. “Same model” is not a controlled architecture comparison: Direct runner/prompt/tools/stopping and token budgets are not included here, and it is unknown whether Direct means a custom minimal runner or a representative coding agent as-is. The requested comparator identity is therefore open, not a proven-invalid baseline. Results are mostly n=1 and stochastic; L1 Direct pass/BID fail did not reproduce (`:182-205`); long-horizon pair reports Direct Done/no files vs BID 576 sec/five-task progression (`:239-260`).
- **Smallest repair direction:** Preserve frozen task, oracle, exact Direct and BID launch code/prompts/tools/budgets, model payloads, raw traces, and outputs in accessible immutable artifacts. Measure tokenized prompt/history vs actual context before claiming physical impossibility; record Direct termination reason only when observable. Identify whether comparator is custom Direct or a named coding agent and avoid treating same model alone as causal control.
- **Validation:** Documentation/source inventory only. The primary assistant's bounded follow-up probe found `veriton` is Windows HOME-PC, WSL lists only `docker-desktop`, and several known local Windows paths for `bid-eval` were absent. This is a bounded location probe, not proof the historical artifacts are unavailable elsewhere on the LAN. No model/search/evaluation call used.
- **Resolution (2026-11-02):** Added `evaluation/repro/` with a frozen example task and deterministic oracle. Historical artifacts were located under `/home/iclab/bid-eval/`; SHA-256 manifests for `repo-context-tools-rehearsal-72cfea4` (126 entries) and `repo-context-tools-rehearsal-f2ae938` (350 entries) match. The Eval 5 long-horizon task manifest matches 2/2 files; its run manifest matches 180 payload files, with one self-reference sentinel. The preserved BID and Direct workspaces were copied into the rootless sandbox and their oracle outcomes reproduced (5 pass/6 fail; 0 pass/1 fail). This does not verify all historical evidence. `benchmark-v2/task.txt` matches its manifest, but the adjacent `hidden_oracle.sha256` sidecar does **not** match the oracle file (recorded `bc704f…`, computed `6b6105…`); both are preserved, and no final benchmark-v2 workspaces were found for replay. The bounded Llama 3.2 1B study completed 45/45 cells: zero binary passes, 45/210 oracle checks, 27/27 paired binary ties, with a custom single-Worker comparator. Subsequent provenance review found all 45 partial checks came from seeded artifacts and all 45 outcomes had unchanged task files; no full BID role loop ran. Epoch 9 is interpreted as a protocol floor/method confound, not an architecture comparison. It does not establish native-agent parity. **Status:** partial, broader historical reproducibility remains open. **Validation:** two named rehearsal manifests and Eval 5 payload manifests verified; Eval 5 oracle pair replayed in the sandbox; benchmark-v2 mismatch remains documented in `HISTORICAL_REPLAY_REPORT.md`.

### F10 — Unreferenced native tool/citation subsystem is disconnected from production

- **Severity/certainty:** Medium / Confirmed-static.
- **Subsystems:** S5, S9.
- **Evidence:** Production Worker directly parses text in `WorkerAdapter.run` (`adapter.py:595-612`) and dispatches built-in command types; `tools.py` and `session.py` define an alternative native function-call path, but repo-wide grep finds no imports/calls from harness/adapters. `BID_TEXT_TOOLS` is used only when creating backend (`harness.py:36-41`); model only injects text tool instructions when `tools` is truthy (`model.py:25-56`), whereas production calls `backend.run(messages, [], ...)` (`adapter.py:565`). `search.has_citations` has no callers (`search.py:292-317`).
- **Root cause and trigger/consequence:** Two generations/interfaces coexist without wiring. Operators may expect the toggle, role tool handlers, or citation enforcement to control the current Worker; they do not. Citation evidence can be stored, but no task/reviewer requirement checks it.
- **Smallest repair direction:** Remove clearly dead alternate code/config or deliberately wire one interface and test it end-to-end; connect citation verification only where a task explicitly requires it.
- **Validation:** Static symbol search and caller trace; no tool/session path was invoked as part of the suite.
- **Resolution (2026-11-01):** Removed uncalled `bid/tools.py`/`bid/session.py`, the inert `BID_TEXT_TOOLS` toggle, and unused citation checker. No blanket citation requirement was added. **Status:** fixed by deleting dead paths. **Validation:** repo symbol search and full suite below.

### F11 — merged alias to F2 (not an independent finding)

- F11 is retained as a stable historical identifier and fully merged into F2, including the retry-feedback and unknown-command surfaces. Count F2/F11 as one issue; do not treat F11 as an independent defect or reuse the identifier.
- **Resolution (2026-11-01):** Same protocol-list correction as F2. **Status:** fixed alias, not an independent issue. **Validation:** F2 command-recovery tests.

### F12 — Valid Manager REWORK decisions can create an unbounded in-process loop

- **Severity/certainty:** High / Confirmed by deterministic bounded reproduction; the unlimited duration is inferred from the loop/state transitions.
- **Subsystems:** S3, S8, S10, S11.
- **Evidence:** Manager reconciliation REWORK invalidates/reopens a provisional suffix in `harness.py:542-579`; the project loop then clears current-task tracking and continues at `harness.py:698-712`. Only Task Reviewer REWORK increments `rework_counts` (`harness.py:760-794`), and ACCEPT clears it (`harness.py:805-808`). No counter/bound is applied to Manager's valid `# Rework` decision.
- **Root cause and trigger/consequence:** Separate retry paths are accounted differently. With an always-ACCEPT Task Reviewer and a Manager that validly returns REWORK for the same task each reconciliation, each cycle submits/reviews another candidate and loops again. This is not the same as F6: it occurs within one process and is not fixed by persisting the Task Reviewer count. The documented `BID_MAX_TASK_REWORKS` specifically names Task Reviewer reworks, so this is an unbounded-work/reliability gap rather than proof that that narrower setting is misimplemented.
- **Smallest repair direction:** Decide whether Manager-driven rework is intentionally unbounded; if not, persist/enforce a separate reconciliation/rework budget or overall run budget and expose terminal state.
- **Validation:** Reproduced with a deterministic fake backend in an isolated temporary workspace: one valid Manager REWORK caused a second Worker submission and second Task Reviewer ACCEPT; a test-only backend exception stopped the next reconciliation retry sequence. No infinite/live run was launched.
- **Resolution (2026-11-01):** Added a distinct persistent `BID_MAX_MANAGER_REWORKS` limit; Manager retries do not consume the Task Reviewer budget. **Status:** fixed. **Validation:** `test_manager_rework_has_a_separate_persistent_limit`.

### F13 — Task Reviewer identifies a candidate snapshot but diffs the live workspace

- **Severity/certainty:** Medium / Confirmed conditional behavior; normal single-process path generally has workspace equal to candidate.
- **Subsystems:** S3, S7, S8.
- **Evidence:** `TaskReviewAdapter` stores `candidate_state` at `adapter.py:1132-1138` and places its ID in the prompt at `1155-1164`, but obtains the diff with `_workspace_diff(base_root, self.workspace)` at `1155`, not the candidate snapshot path. The harness has no project-wide lock around candidate submission/review; VC locking is limited to individual file operations (`vc.py:21-37`).
- **Root cause and trigger/consequence:** Review identity and review bytes come from different sources. If another process or external actor edits the workspace between candidate snapshot and review, the reviewer evaluates those live edits while the prompt names the saved candidate. It can approve a different state than the one recorded as the provisional candidate. The ordinary sequential path does not establish that this occurs without concurrent/external mutation.
- **Smallest repair direction:** Build the review diff from the submitted candidate snapshot and/or serialize project-level execution; add a test that mutates the live workspace between submit and review.
- **Validation:** Reproduced conditionally in a temp workspace by saving candidate `s1`, changing the live file, then invoking review: prompt named `s1` but contained the intervening live edit. No concurrent project process was launched.
- **Resolution (2026-11-01):** Reviewer validates base/candidate IDs before path construction and builds the diff from the saved candidate snapshot, not live workspace bytes. **Status:** fixed. **Validation:** `test_reviewer_prompt_includes_identity_and_verification_status` asserts candidate bytes appear and intervening live bytes do not; invalid and missing base/candidate tests assert the backend is not called.

### F14 — A crash can leave a permanent stale VC lock that blocks recovery

- **Severity/certainty:** High / Confirmed-static and reproduced with an orphaned lock marker.
- **Subsystems:** S7, S8.
- **Evidence:** `vc.py:9` sets 30-second timeout; `_acquire_lock` creates `.bid/lock` with `O_EXCL` and retries existing locks (`vc.py:21-31`); `_release_lock` removes it only when reached (`vc.py:33-37`). There is no PID/owner/lease validation or stale-lock recovery. Mutations of snapshots/log use this lock.
- **Root cause and trigger/consequence:** Process death while holding the lock skips `finally` cleanup; every later writer waits then fails after 30 seconds, across application restarts. This blocks otherwise available rollback/recovery and persists until manual deletion.
- **Smallest repair direction:** Use an OS lock that releases on process exit, or add safe owner/liveness recovery with clear limits; do not blindly remove a live process's lock.
- **Validation:** Reproduced in a temporary `.bid` tree with a pre-existing orphan marker and temporarily shortened timeout: two successive log append attempts both timed out and the marker remained.
- **Resolution (2026-11-01):** Replaced marker ownership with OS-managed `flock`/`msvcrt` byte-range locking. Process termination releases the lock; init preserves the locked file rather than unlinking a live owner. **Status:** fixed on supported Windows/Unix runtimes. **Validation:** `test_os_lock_blocks_live_owner_and_releases_on_process_exit`.

### F15 — README installation command fails from the checked-in flat layout

- **Severity/certainty:** High / Confirmed in isolated package-copy build.
- **Subsystems:** S1, S4.
- **Evidence:** README directs `pip install -e .` (`README.md:11-17`); `pyproject.toml:1-14` has no explicit build backend/package discovery configuration. Setuptools discovery in the repository's flat layout treats `bid` and `prompts` as top-level packages and aborts.
- **Root cause and trigger/consequence:** Namespace-style discovery sees the prompt-data directory as a second top-level package. Fresh users following installation instructions cannot install the project in editable form, so the documented quick start is blocked before CLI use.
- **Smallest repair direction:** Explicitly configure package discovery to include only the intended Python package and package prompt data or otherwise keep prompt assets available; verify with both editable install and wheel build.
- **Validation:** In a temp copy of tracked package inputs, both `pip install --no-deps --editable <copy>` and `pip install --no-deps --target <dir> <copy>` failed at “Getting requirements to build” with `Multiple top-level packages discovered in a flat-layout: ['bid', 'prompts']`. No build/install ran against the repository.
- **Resolution (2026-11-01):** Declared explicit package discovery, included prompt Markdown assets, made `pyproject.toml` the sole version source, and added the installed `bid` console script used by README. **Status:** fixed. **Validation:** clean-copy editable/wheel install and installed CLI checks below.

### F16 — Review evidence loses exact newline information

- **Severity/certainty:** Medium / Confirmed by regression checks; an actual marker calibration also exposed false Manager claims about final-newline state.
- **Subsystems:** S3, S8, S10, S11.
- **Evidence/root cause:** `_workspace_tree` used universal-newline text reads, normalizing CRLF, while `_workspace_diff` used line-based diffs and unannotated added-file text. Exact candidate bytes and final-newline-only differences were not reliably represented in the review handoff.
- **Resolution (2026-11-02):** Preserve raw newlines when reading review trees and annotate each changed file with compact exact UTF-8 byte length and LF/CRLF/none state. Keep the existing per-file detail budget regression passing; do not use a hidden oracle to force acceptance. **Status:** fixed in source epoch 18. **Validation:** three newline/byte-count cases plus the existing late-file sentinel tests; 189 Ubuntu tests passed before model access. The Manager can still misjudge correct evidence, a model-quality limitation rather than a hidden automatic quality gate.

### F17 — Host-side study file handling crossed symlink and special-file boundaries

- **Severity/certainty:** High / Confirmed in the study's host-side paths; exploitability is bounded by the filesystem entries a model-created workspace can produce.
- **Subsystems:** S6, S10, S16.
- **Evidence and root cause:** Epoch-18 review found snapshot/restore, observation, review, and artifact-hash paths that could follow model-created symlinks outside the disposable workspace; opening special entries such as FIFOs could also block host-side processing. Sandbox isolation alone did not constrain these host operations.
- **Resolution (2026-11-02):** Preserve symlinks as links when copying/restoring and describe/hash the link itself; reject or skip special files rather than opening them; reject symbolic control roots before initialization. The trusted namespace PID 1 supervises model commands as children so session-detaching descendants remain within timeout cleanup. **Status:** corrected for tested paths; not a comprehensive filesystem-race or kernel-security guarantee.
- **Validation:** Primary verified immutable epoch-20 manifest `e925ca9e3d3e6c3c6e9cb6f9be19e04aa9f171bffd74ba4e6299d0e12d20c051` and ran the focused Linux symlink/FIFO/worker-executor/session-detaching-timeout/artifact-hash checks: 9 passed, 9 deselected in 1.48s. Recorded broad candidate suites: Ubuntu 197 passed; Windows 175 passed, 10 capability-specific skips. These are bounded regression checks, not a security certification.

### F18 — Repeated multi-command Worker turns evade stall detection

- **Severity/certainty:** Medium / Confirmed by regression and real-model reproduction.
- **Subsystems:** S3, S8.
- **Root cause:** Stall detection compared only the most recent individual command. A repeated turn containing different commands (for example READ followed by WRITE) reset that signature on every READ, while successful no-op writes were marked useful. The repeated turn therefore never reached the configured repeat limit even though the workspace stopped changing.
- **Resolution (2026-11-02):** Count identical whole-turn command signatures only when the turn makes no workspace or read/search progress; successful writes/replacements rely on observed file changes rather than being treated as useful by themselves. The existing soft-reset and Worker retry limits remain in force. **Status:** fixed for repeated command sequences and no-op edits; not a guarantee against all changing but unproductive model output.
- **Validation:** New regression `test_repeated_multicommand_turn_stalls_and_rolls_back` failed before the fix and passes after it; `test_init_to_manager_ratified_artifact` covers init → plan → Worker → Reviewer → Manager ratification and final persisted artifacts. Linux full suite: 187 passed. Windows suite: 177 passed, 10 capability-specific skips. A real 3B normal-product-prompt smoke reproduced the old repeat gap; after the fix its first Worker was bounded as stalled, but subsequent malformed REPLACE output exhausted the separate smoke budget before review. See `evaluation/development-smoke/REPORT.md`; this is not a successful real-model full loop.

## Primary continuation: epochs 14–18

- The original 45-cell 1B cohort never reached review/reconciliation. Its 45 passed individual checks were already true in seed workspaces; they were not task improvements. Preserve it as a protocol-floor result, not a coordination or batch-size comparison.
- Primary implemented and tested a persistent isolated executor, optional GBNF request support, and a separately declared constrained text protocol. No default host trust was enabled. Source snapshots, calibration plans, traces and failures remain separate under `primary-phase14b`, `primary-phase15`, `primary-phase16`, and `primary-phase18` on Q12.
- Phase 14 failed to submit a transient correct write. Phase 15 reached review and reconciliation but wrote escaped, incorrect text. Phase 16 produced the correct artifact and real role coverage but suffered false Manager rework and unbounded optional descriptions. Phase 17 failed a unit evidence-budget regression before model access; phase 18 corrected it and passed 189 Ubuntu checks.
- Phase 18 calibration independently passed its four-byte artifact oracle and exercised Manager, Worker, Reviewer, and reconciliation, but the Manager falsely requested rework until its limit. Do not report this as harness completion. **Closeout correction (2026-11-02):** the prospective 45-cell 3B/Q8 cohort in `/home/iclab/bid-studies/2026-11-01-llama1b/primary-matrix18/` was safety-stopped with 17 completed outcomes, one interrupted outcome, and 27 pending. No epoch-20 matrix was launched. These counts are recorded from the preserved plan and prior safety-stop record, not recomputed during closeout; the delegated evidence export did not complete.
- The current local follow-up hardens sandbox PID-1 supervision against session-detaching descendants. It is not part of the frozen epoch-18 source. All changes remain local/unpublished: no staging, commits or pushes.

## Contradictions, quality limits, and positive behaviors

- **Manager/Worker model identity vs research comparison:** Source confirms Manager/Worker/Reviewer share one configured backend/model; prompts differ, model does not. This is relevant to the experiment but does not answer whether this architecture improves coding-agent performance. Endpoint may be remote despite local-first default. Historical EVAL reports the same Qwen 4B payload for BID/Direct (`EVALUATION.md:63-65,149-150`), but evidence and exact Direct comparator are unavailable here; do not equate same model with architecture benefit.
- **Review vs verification:** Task Reviewer prompt explicitly says no harness-owned verification was executed (`adapter.py:1155-1168`). It judges a model-written diff; it is not tests/oracle. Evaluation already documents a false positive: pytest collected zero tests yet reviewer and Manager accepted/ratified (`EVALUATION.md:44-49`). This is an acknowledged ceiling, not a hidden independent quality gate.
- **Protocol docs:** README advertises SEARCH while Worker prompt/retry feedback omit it; REPLACE exists in prompt but is excluded from recovery lists (F2; F11 is merged alias only). README calls VC log append-only while manual restore truncates it (F4). README says `BID_TEXT_TOOLS` enables text protocol, but actual Worker parser is independent and native backend gets `tools=[]` (F10). README “version control” is BID snapshot versioning, not Git integration (README 133; `vc.py`).
- **Evaluation comparability:** EVAL honestly distinguishes artifact oracle from BID terminal status (L2 false-negative), keeps negative results, acknowledges n=1 and non-reproducibility, and revises interpretation of short tasks. At the same time, changing the target hypothesis after short-task outcomes (EVALUATION lines 210-229) makes conclusions about purpose exploratory; Eval 5 remains both-fail/no demonstrated benefit. No in-repo scorer/baseline harness makes comparisons repeatable.
- **Positive behaviors verified statically:** fixed-base diff review in ordinary sequential operation, candidate snapshots, deterministic task IDs, Manager-only ratification, validation-before-reconcile mutation, exact-occurrence REPLACE, traversal-aware `realpath` checks, argv not implicitly sent through a shell, protected-control snapshots around RUN, bounded search output/cache and untrusted labeling, bounded retries for malformed planner/reviewer/reconcile outputs, and negative evaluation reporting. F12 and conditional F13 qualify the boundedness and candidate-review picture; these guardrails do not mitigate F1 host execution authority or guarantee crash-atomic transitions.

## Historical audit checks and results (baseline commit `1399767f1a117f22becf40afade71885baf18afd`)

The findings, test outcomes, expected failures, commands, and reproductions in this section record the pre-fix audit behavior. They are retained as history, not as current verification. Appendix A deliberately reproduces the old bugs and is expected to FAIL against the fixed tree; do not run it expecting its baseline assertions to pass.

- `git status --short --untracked-files=all` before audit: clean. Commit above.
- `git ls-files`: inspected; no benchmark evidence/oracle files, no `test_session.py` source, no CI beyond `.github/workflows/tests.yml`. Existing pycache bytecode tracked and left untouched.
- Initial runtime probe found only WindowsApps stubs; after authorization, `winget install --id Python.Python.3.12 --source winget --scope user --silent ...` installed Python 3.12.10. `pytest 9.2.0` and `httpx 0.28.1` were installed only in `C:\Users\hind\AppData\Local\Temp\opencode\bid-audit-venv`.
- First suite attempt without UTF-8 mode: 105 passed / 43 failed; most were cascading cp1252 encoding failures where test-created en dash bytes `0x97` were decoded as UTF-8. Rerun with `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`, pytest cache disabled, and temp roots redirected under approved `opencode`: **148 collected, 143 passed, 5 failed**. Four failures are existing `TestInitProject` symlink setup errors (`WinError 1314`, unprivileged Windows symlink creation); one existing `test_direct_deletions_allow_ordinary_project_paths` expects Unix `rm` on Windows. CI is Ubuntu and was not independently run. Exact failures: `tests/test_harness.py::TestInitProject::{test_existing_project_is_preserved_and_snapshotted,test_worker_can_read_existing_project_file,test_manager_failure_restores_existing_project,test_copy_failure_restores_existing_project}` and `tests/test_harness.py::TestWorkerSession::test_direct_deletions_allow_ordinary_project_paths`. These are platform-specific existing tests in this unchanged checkout, not demonstrated product-code regressions.
- Deterministic temporary repros all passed: F3 backup sentinel deletion; F5 ratification interruption/recovery failure; F6 cap bypass across two restarts; F7 fake-clock ceiling overrun; F12 Manager REWORK into a second Worker/ACCEPT; F13 intervening live edit in review diff; F14 orphan lock blocks repeated writes. No infinite/live loop was launched.
- Package check used a temporary copy of source inputs: both `pip install --no-deps --editable <copy>` and `pip install --no-deps --target <dir> <copy>` failed at build-requirements discovery with `Multiple top-level packages discovered in a flat-layout: ['bid', 'prompts']` (F15); no install/build was attempted against the checkout.
- First test attempt created event-log sibling folders under default system temp because direct `tempfile.TemporaryDirectory()` bypassed pytest basetemp; identified and removed only 182 `tmp*-events` directories from the exact test-run windows. Final test run set `TEMP`, `TMP`, and `TMPDIR` to `opencode`. `PYTHONDONTWRITEBYTECODE=1` used; no repository cache/bytecode created.
- No live model/search/evaluation call or worker against actual workspace. Read-only SSH/location probes and authorized package downloads were the only external-access setup.

### Historical baseline verification recipe (Windows PowerShell; not a current-fix check)

The checked-out source is expected at `C:\projects\breakingitdown`; the per-user Python 3.12.10 venv and test dependencies used for this audit are under `C:\Users\hind\AppData\Local\Temp\opencode\bid-audit-venv`. Set the test environment and rerun the suite with:

```powershell
Set-Location 'C:\projects\breakingitdown'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = 'C:\projects\breakingitdown'
$env:TEMP = 'C:\Users\hind\AppData\Local\Temp\opencode'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
& 'C:\Users\hind\AppData\Local\Temp\opencode\bid-audit-venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --basetemp='C:\Users\hind\AppData\Local\Temp\opencode\bid-audit-primary-pytest'
```

Historical baseline result only: 143 passed, 5 failed. This is not the expected result on the fixed tree. The four symlink-creation failures and Unix `rm` failure were baseline test issues addressed by later changes. The seven bug reproductions in Appendix A are not current regression tests. The prior file `C:\Users\hind\AppData\Local\Temp\opencode\bid_audit_repros.py` was ephemeral and is not checked in. The command below runs the historical reproductions, which are expected to fail against the fixed tree:

```powershell
& 'C:\Users\hind\AppData\Local\Temp\opencode\bid-audit-venv\Scripts\python.exe' 'C:\Users\hind\AppData\Local\Temp\opencode\bid_audit_repros.py'
```

Historical baseline expectation only: all seven bug reproductions passed against the unfixed behavior. They are intentionally bug-asserting and expected to FAIL against the fixed tree. No current-agent should treat them as a passing verification recipe. This ledger records 14 independent findings (F1-F10 and F12-F15); F11 is a merged alias of F2. The subsystem register contains S1-S13.

## Authorized fix verification (2026-11-01)

- Focused regression suites (`tests/test_harness.py tests/test_vc.py`): **160 passed, 4 skipped**.
- Full Python 3.12 suite with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUTF8=1`, pytest cache disabled, and temp paths under `C:\Users\hind\AppData\Local\Temp\opencode`: **169 passed, 4 skipped**. The skips are only symlink tests where Windows privilege is unavailable.
- Full suite without `PYTHONUTF8`: **169 passed, 4 skipped**; text-mode test I/O defaults explicitly to UTF-8 through `tests.open_utf8`.
- Packaging ran only from a temporary source copy: wheel build succeeded; editable and wheel installs succeeded in separate temporary virtual environments. From outside the checkout, both imports reported matching distribution/runtime version `0.2.0rc1` and loaded `prompts/worker.md`; installed `bid` entry point printed usage. No install/build output was written into the repository.
- Independent final primary review: exact full-suite command below completed with **169 passed, 4 skipped in 12.71s**. An isolated installed-wheel check from outside the checkout, using `python -I -B`, confirmed the import came from wheel `site-packages`, `bid.__version__ == importlib.metadata.version('breakitdown') == '0.2.0rc1'`, and packaged `prompts/worker.md` contains `SEARCH`. **Passed.** `git diff --check` passed.
- F9 fixture oracle smoke invocation passed. Historical evidence and Direct runner identity remain unavailable and are not claimed verified.
- CI now covers Ubuntu and Windows on Python 3.11/3.12 with editable install; hosted CI was not run from this session.
- No live model call, no run against the checked-in workspace, and no real user data mutation. No commit made.

Independent final primary full-suite command:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONUTF8='1'; $env:PYTHONPATH='C:\projects\breakingitdown'; $env:TEMP='C:\Users\hind\AppData\Local\Temp\opencode'; $env:TMP=$env:TEMP; $env:TMPDIR=$env:TEMP; & 'C:\Users\hind\AppData\Local\Temp\opencode\bid-audit-venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --basetemp='C:\Users\hind\AppData\Local\Temp\opencode\bid-fixes-primary-pytest'
```

## Audit-time prioritized follow-up / continuation rules

1. **F1, F3, F5, F6, F12, F14:** address trust boundary, backup data loss, crash recovery, both reviewer/manager retry bounds, and stale-lock risk before presenting the harness as reliably bounded on arbitrary workspaces.
2. **F15, F2, F10:** make documented install work and reconcile command/protocol surfaces; decide whether disconnected native tools/citation paths should be removed or wired.
3. **F4, F7, F8, F13:** align durability, timeout, release identity, and submitted-candidate review semantics.
4. **F9:** make historical evidence accessible, identify the Direct comparator, support horizon-fit statements with token/context evidence, and run replicated budget-matched comparisons before architecture-benefit claims.
5. Run the suite on supported Ubuntu CI to distinguish the five Windows-only failures; add fault-injection coverage for interrupted ratification, repeated Manager REWORK, stale lock, and review candidate/live divergence if product work is authorized.

## Change log / handoff

- 2026-11-01 initial pass: Created ledger after verifying clean baseline/no pre-existing ledger. Registered S1–S13 and F1–F11; static tracing only, tests blocked by unavailable interpreter. This earlier blocked-check note is historical and superseded below.
- 2026-11-01 continuation: Corrected the comparison goal; added explicit file inventory; merged F11 into F2 as a stable alias; added F12-F15 for Manager REWORK loop, candidate/live diff mismatch, stale VC lock, and reproducible install failure. Added test results, isolated reproductions, and the primary assistant's bounded evidence-location probe. No repo writes beyond this ledger.
- Historical, pre-authorization 2026-11-01 final primary review: Ran all seven baseline bug reproductions and the original suite; those results and the prior “product changes remain unauthorized” handoff describe only the audit baseline, not current status.
- 2026-11-01 authorized product-fix pass: Applied fixes for F1-F8, F10, and F12-F15; F9 is partially addressed because historical artifacts remain unavailable; F11 retains its alias note. Updated this ledger under every finding, retained historical evidence and Appendix A, added a smoke fixture, and ran full-suite, isolated package, and oracle checks. No commit made.
- Independent final primary verification: recorded current full suite, isolated wheel/runtime version and packaged prompt checks, and `git diff --check` above. No code changed in this documentation-only update; no additional tests were run.

## Appendix A: historical baseline bug reproductions (expected to fail against fixed tree)

**Historical only.** This script encodes the unfixed bugs' expected behavior and intentionally FAILS against the fixed tree (starting with F3). Do not use it to verify current fixes or expect its assertions to pass. It is retained to document how the original audit reproduced the baseline findings. Each workspace is temporary; the fake backend's exception bounds F12 without an infinite loop.

```python
import os
import tempfile
from pathlib import Path

from bid import adapter, harness, model, todo, vc

TEMP_ROOT = Path(r"C:\Users\hind\AppData\Local\Temp\opencode")


def response(content):
    return {"role": "assistant", "content": content, "finish_reason": "stop"}


def config(workspace, **overrides):
    return {
        "workspace": str(workspace), "max_tokens": 128,
        "request_timeout": 1, "worker_timeout": 1,
        "inactivity_timeout": 60, "repeat_action_limit": 50,
        "run_timeout": 1, "max_searches_per_worker": 0,
        "provisional_batch": 1, "max_task_reworks": 1,
        **overrides,
    }


def prepare(workspace, marker=" "):
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs/todo.md").write_text(
        f"- [{marker}] T1 - Task\n", encoding="utf-8"
    )
    (workspace / "docs/task.md").write_text("Task\n", encoding="utf-8")
    (workspace / "docs/project-status.md").write_text("Init\n", encoding="utf-8")
    (workspace / "docs/decisions.md").write_text("\n", encoding="utf-8")
    harness.ensure_workspace(str(workspace))
    vc.VersionControl(str(workspace)).init()


# F3: init deletes a pre-existing sibling backup path.
with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
    parent = Path(directory)
    workspace = parent / "project"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("project", encoding="utf-8")
    backup = parent / ".bid_backup"
    backup.mkdir()
    sentinel = backup / "user-data.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    result = harness.init_project(
        "Task", config(workspace), model.MockBackend([response("- [ ] Task")])
    )
    assert result["status"] == "success" and not sentinel.exists()
print("F3 passed")


# F5: interrupt after ratified log append but before TODO write.
with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
    workspace = Path(directory)
    prepare(workspace, "-")
    store = vc.VersionControl(str(workspace))
    candidate = store.save_state("candidate", "T1")
    store.append_log(
        f"provisional: task=T1 base=s0 candidate={candidate} review=ACCEPT"
    )
    active = {1: {"base": "s0", "candidate": candidate}}
    todo_path = workspace / "docs/todo.md"
    tasks = todo.parse_todo(todo_path.read_text(encoding="utf-8"))
    original_write = harness.write_file_content

    def interrupt_todo(path, content):
        if os.path.basename(path) == "todo.md":
            raise RuntimeError("simulated interruption")
        return original_write(path, content)

    harness.write_file_content = interrupt_todo
    try:
        try:
            harness._apply_reconcile_decision(
                store, str(workspace), todo_path.read_text(encoding="utf-8"),
                tasks, active,
                {"done": [1], "rework": None, "add": [], "replace": None,
                 "project": "COMPLETE"},
                {},
            )
        except RuntimeError:
            pass
    finally:
        harness.write_file_content = original_write
    recovered, _, error = harness._resolve_provisional_state(
        store, todo_path.read_text(encoding="utf-8")
    )
    assert recovered is None and "without a valid provisional record" in error
print("F5 passed")


# F6: each run restart resets the Task Reviewer rework counter.
with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
    workspace = Path(directory)
    prepare(workspace)

    class AlwaysRework(model.MockBackend):
        def __init__(self):
            super().__init__([])

        def run(self, messages, tools, max_tokens=None):
            prompt = messages[1]["content"]
            if prompt.lstrip().startswith("Task T1:"):
                return response("Done")
            if prompt.startswith("# Review Assignment"):
                return response("REWORK\nReason: no")
            raise AssertionError(prompt[:80])

    backend = AlwaysRework()
    settings = config(workspace, max_task_reworks=1)
    first = harness.run_project(settings, backend)
    second = harness.run_project(settings, backend)
    log = vc.VersionControl(str(workspace)).get_log()
    assert first["status"] == second["status"] == "error"
    assert log.count("rework_limit: task=T1 count=2 limit=1") == 2
print("F6 passed")


# F7: a blocking model response can finish beyond the session ceiling.
with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
    workspace = Path(directory)
    prepare(workspace)
    clock = {"time": 0.0}
    original_clock = adapter.time.monotonic

    class SlowBackend:
        def run(self, *args, **kwargs):
            clock["time"] += 5.0
            return response("Done")

    adapter.time.monotonic = lambda: clock["time"]
    try:
        result = adapter.WorkerAdapter(
            config(workspace, worker_timeout=1), 1
        ).run(SlowBackend())
    finally:
        adapter.time.monotonic = original_clock
    assert result["status"] == "done" and clock["time"] > 1
print("F7 passed")


# F14: an orphaned lock marker blocks later VC mutations.
with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
    workspace = Path(directory)
    (workspace / ".bid/states").mkdir(parents=True)
    (workspace / ".bid/lock").write_text("dead owner", encoding="utf-8")
    original_timeout = vc._LOCK_TIMEOUT
    vc._LOCK_TIMEOUT = 0.01
    try:
        store = vc.VersionControl(str(workspace))
        errors = []
        for _ in range(2):
            try:
                store.append_log("event")
            except RuntimeError as error:
                errors.append(str(error))
        assert len(errors) == 2 and (workspace / ".bid/lock").exists()
    finally:
        vc._LOCK_TIMEOUT = original_timeout
print("F14 passed")


# F13: reviewer prompt names candidate snapshot but uses an intervening live edit.
with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
    workspace = Path(directory)
    prepare(workspace)
    (workspace / "out.txt").write_text("candidate", encoding="utf-8")
    store = vc.VersionControl(str(workspace))
    candidate = store.save_state("candidate", "T1")
    (workspace / "out.txt").write_text("concurrent-edit", encoding="utf-8")

    class CaptureReviewer:
        def run(self, messages, *args, **kwargs):
            self.prompt = messages[1]["content"]
            return response("ACCEPT\nReason: test")

    reviewer = CaptureReviewer()
    adapter.TaskReviewAdapter(
        config(workspace), 1, base_state="s0", candidate_state=candidate
    ).run(reviewer)
    assert f"Submitted candidate: {candidate}" in reviewer.prompt
    assert "concurrent-edit" in reviewer.prompt
    assert "candidate\n" not in reviewer.prompt
print("F13 passed")


# F12: one Manager REWORK leads to another Worker/ACCEPT; injected error bounds run.
with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
    workspace = Path(directory)
    prepare(workspace)

    class ManagerLoop(model.MockBackend):
        def __init__(self):
            super().__init__([])
            self.workers = self.reviews = self.managers = 0

        def run(self, messages, tools, max_tokens=None):
            prompt = messages[1]["content"]
            if prompt.lstrip().startswith("Task T1:"):
                self.workers += 1
                return response("Done")
            if prompt.startswith("# Review Assignment"):
                self.reviews += 1
                return response("ACCEPT\nReason: ok")
            if prompt.startswith("# Manager Reconciliation"):
                self.managers += 1
                if self.managers == 1:
                    return response("# Rework\n- T1 - redo\n# Project\nCONTINUE")
                raise RuntimeError("bounded reproduction stop")
            raise AssertionError(prompt[:80])

    backend = ManagerLoop()
    result = harness.run_project(config(workspace), backend)
    assert result["status"] == "error"
    assert (backend.workers, backend.reviews, backend.managers) == (2, 2, 4)
    assert not any(
        line.startswith("rework_limit:")
        for line in vc.VersionControl(str(workspace)).get_log().splitlines()
    )
print("F12 passed")
```

## Overnight experiment closeout (2026-11-02)

- Hard cutoff was 08:00 Taipei. No inference, calibration, matrix, launcher, or product-code change was made during this closeout.
- The stale “matrix18 running” status above is superseded: safety cohort counts are 17 completed outcomes, one interruption, and 27 pending. Epoch 20 was planned only; no new comparative cohort completed. There is no native coding-agent baseline.
- The epoch-20 candidate manifest hash recorded locally is `e925ca9e3d3e6c3c6e9cb6f9be19e04aa9f171bffd74ba4e6299d0e12d20c051` (36 source/fixture files). Recorded checks: Ubuntu 197 passed; Windows 175 passed, 10 capability-specific skips. These are candidate test results, not experiment outcomes.
- Boundary correction: host-side snapshot/restore, observation, review, and hashing must preserve symlinks as links and reject special files instead of following/opening them. Trusted namespace PID 1 now supervises model commands as child processes so session-detaching descendants remain under timeout cleanup. This is bounded hardening, not a kernel security audit or aggregate resource-limit claim.
- Primary independently checked runtime state at 2026-11-02 09:52 Taipei: no active study PIDs; owned llama-server PID 8611 was the expected Q8_0 3B binary/model on localhost:8081 and was stopped with SIGTERM; no llama-server remained. GPU afterward: 391 MiB used / 15,605 MiB free of 16,303 MiB. See `evaluation/studies/2026-11-01-llama1b/FINAL_RUNTIME_STATE.json` (primary-created; not modified here).
- The delegated evidence-export attempt did not complete in its execution environment; its cause is not established here. The primary successfully used SSH and remote Ubuntu-22.04 to verify the epoch-20 manifest and focused Linux checks, and independently verified the runtime shutdown. Remote SAFETY_STOP, summaries, and calibration outcome files were not exported into this checkout; the raw epoch-18 queue was not recomputed. The 17/1/27 outcome counts remain recorded from the closeout instruction and local plan, not newly verified here. No home scan or model-created-link traversal was used. Existing local epoch-9–13 manifests and calibration outcomes remain archived.
- Local regression check: Python 3.12 Windows full suite **175 passed, 10 skipped** in 14.82s. This is separate from the Ubuntu epoch-20 result above. `git diff --check` result is recorded in the closeout report.

## Independent corrected-source verification (2026-11-02)

- Primary independently verified the corrected source snapshot at `/home/iclab/bid-studies/2026-11-01-llama1b/development-primary-verify-2026-11-02/source`, manifest SHA-256 `f4e87c40701c449c9b1f6afc35fb7aa99502e2839882257d204a8b6264e72c2a`.
- Ordinary Qwen3-4B Q8_0 CLI task created `arithmetic.py` and importing `main.py`; init/run both exited 0. Manager 1, Worker 4, Reviewer 2, reconciliation 3. Both tasks are `[x]` and ratified.
- Existing isolated `run_python` checks on the copied workspace returned `[-4, 0, 10]` for `double(-2, 0, 5)` and ran `main.py` with exit 0 and stdout `42\n`. Linux product suite: 195 passed in 2.83s. Batch 1; grammar and host RUN off.
- The owned server stopped in `finally`; GPU afterward was 390 MiB used and 15,606 MiB free. This verifies one bounded real end-to-end path, not general task reliability or an architecture benefit. Full record: `evaluation/development-smoke/primary-verification-2026-11-02/`.
