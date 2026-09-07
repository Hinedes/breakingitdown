# BID — Break It Down

**Local-first long-horizon agent harness for small and edge language models.**

```text
user task → Manager plan → Worker candidate → Task Reviewer → provisional state → Manager reconciliation → ratified work
```

BID keeps project continuity outside the Worker. The harness owns the plan, snapshots, task identity, rollback, reviewer evidence, reconciliation state, and recovery across restarts; each Worker only receives one bounded task at a time.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+ and `httpx>=0.27`.

## Quick start

```bash
export BID_MODEL_ENDPOINT="http://127.0.0.1:8080/v1/chat/completions"
export BID_MODEL_NAME="your-model"
export BID_WORKSPACE="./my-task"

python bid.py init "Your task description here"
python bid.py run
```

Continue an existing project with:

```bash
python bid.py run
# or
python bid.py resume
```

Inspect state with:

```bash
python bid.py status
python bid.py vc log
python bid.py vc rollback <state>
```

## Execution model

### 1. Manager planning

`bid.py init` creates the BID workspace, records the user request, initializes version control, and asks the Manager to produce a sequential Markdown checklist in `docs/todo.md`.

Task states are:

- `[ ]` — unchecked and executable
- `[-]` — provisional candidate admitted by the Task Reviewer
- `[x]` — ratified work accepted by Manager reconciliation

Task numbers are canonical and sequential: `T1`, `T2`, `T3`, ...

### 2. Bounded Worker session

A Worker receives one unchecked task and operates inside the workspace through BID commands:

```text
READ <path>
SEARCH <query>
RUN <program> [args...]
WRITE <path>
<content>
END WRITE
REPLACE <path>
<old text>
---REPLACE_WITH---
<new text>
END REPLACE
Done
```

`READ` reads workspace files. `WRITE` replaces a file with supplied content. `REPLACE` requires one exact occurrence of the old text. `RUN` executes an argv-parsed command in the workspace with a per-command timeout and records stdout, stderr, exit code, and timeout state as evidence.

`SEARCH` performs a bounded web search and stores the result under:

```text
docs/research/T<task>/search-<n>.md
```

Search results are cached persistently under `.bid/search_cache/`, and provider output is stored as explicitly untrusted source material.

The Worker submits its candidate with `Done`.

### 3. Candidate snapshot

A completed Worker session is saved as a named workspace snapshot. BID records the task base state, candidate state, and accumulated `RUN` evidence.

### 4. Task Reviewer

The Task Reviewer receives the original request, assigned task, and fixed-base → candidate diff. It returns either:

```text
ACCEPT
Reason: ...
```

or:

```text
REWORK
Reason: ...
```

A rejected candidate is rolled back to its fixed base. An accepted candidate becomes provisional rather than immediately DONE.

### 5. Manager reconciliation

When the provisional batch reaches `BID_PROVISIONAL_BATCH`, or no unchecked task remains, the Manager returns to reconcile the current provisional work.

The reconciliation decision can:

- ratify provisional tasks as DONE
- request REWORK
- add new tasks
- replace the remaining unchecked plan
- declare the project `CONTINUE` or `COMPLETE`

The harness validates the complete Manager decision before mutating project state.

Ratification changes `[-]` to `[x]`. A REWORK restores the selected task's fixed base, invalidates the affected provisional suffix, and reopens the corresponding work.

Only Manager reconciliation ratifies permanent DONE state and only the Manager declares project completion.

## Persistent state and recovery

BID uses its own snapshot/version-control layer under `.bid/`.

The append-only VC log records durable task transitions such as:

```text
provisional: task=Tn base=sX candidate=sY review=ACCEPT
ratified:    task=Tn base=sX candidate=sY
invalidate:  task=Tn base=sX candidate=sY reason=...
```

The VC log is authoritative for provisional identity. On restart, BID reconciles `docs/todo.md` with the persisted log, repairs interrupted admission state when the durable record is present, and validates that referenced base/candidate snapshots still exist before continuing.

Workspace snapshots are retained for candidate submission, reconciliation, rollback, and replay.

An append-only JSONL observability log records model requests, commands, timings, token usage, transitions, candidate submission, rollback, and reconciliation events.

## Workspace protection

BID separates project files from harness control state. Worker access is checked against workspace-relative path rules.

Protected control roots include BID state and orchestration files such as:

```text
.bid/
docs/task.md
docs/todo.md
docs/worker.md
docs/manager.md
docs/project-status.md
docs/decisions.md
docs/reviews/
```

`RUN` commands are parsed into argv rather than passed through a shell. Direct deletion commands are checked against workspace and protected paths. BID snapshots protected control state around `RUN` and restores it if a command changes that state.

## Progress control

Worker sessions are bounded by several deterministic controls:

- hard Worker session ceiling
- inactivity timeout
- per-`RUN` timeout
- repeated-action stall detection with soft resets
- maximum web searches per Worker
- maximum Task Reviewer reworks per task
- provisional reconciliation batch size

These values are configurable through environment variables.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `BID_MODEL_ENDPOINT` | `http://127.0.0.1:8080/v1/chat/completions` | OpenAI-compatible chat endpoint |
| `BID_MODEL_NAME` | `smollm3-3b` | Model name sent to the endpoint |
| `BID_WORKSPACE` | `./workspace` | Project workspace |
| `BID_MAX_TOKENS` | `32768` | Maximum tokens per model request |
| `BID_REQUEST_TIMEOUT` | `300` | Model HTTP request timeout, seconds |
| `BID_WORKER_TIMEOUT` | `3600` | Hard Worker session ceiling, seconds |
| `BID_INACTIVITY_TIMEOUT` | `600` | Worker inactivity timeout, seconds |
| `BID_RUN_TIMEOUT` | `60` | Per-command `RUN` timeout, seconds |
| `BID_REPEAT_ACTION_LIMIT` | `5` | Repeated-action threshold before reset |
| `BID_MAX_SEARCHES` | `10` | Search attempts per Worker |
| `BID_SEARCH_ENDPOINT` | empty | Optional search provider endpoint |
| `BID_PROVISIONAL_BATCH` | `4` | Provisional tasks before Manager reconciliation |
| `BID_MAX_TASK_REWORKS` | `3` | Task Reviewer reworks allowed per task |
| `BID_BACKEND` | unset | Set to `mock` for the built-in mock backend |
| `BID_TEXT_TOOLS` | `1` | Enable the text-command Worker protocol |
| `BID_SEARCH_MOCK` | unset | Set to `1` for the mock search provider |

## Project layout

- `bid/harness.py` — orchestration, task lifecycle, reconciliation, persistence recovery
- `bid/adapter.py` — Manager/Worker/Reviewer adapters and Worker command execution
- `bid/vc.py` — workspace snapshots and append-only VC state
- `bid/todo.py` — canonical task parsing and state transitions
- `bid/permissions.py` — workspace path and role permissions
- `bid/search.py` — bounded search, persistent search cache, stored research evidence
- `bid/observability.py` / `bid/observer.py` — event logging and activity/change observation
- `bid/model.py` — model backends
- `prompts/` — Manager and Worker policies
- `tests/` — automated regression and invariant tests
- `EVIDENCE.md` — implementation/evidence record
- `EVALUATION.md` — evaluation record

## Testing

```bash
python -m pytest
```

or install the test extra first:

```bash
pip install -e '.[test]'
python -m pytest
```
