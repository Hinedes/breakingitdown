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

bid init "Your task description here"
bid run
```

Continue an existing project with:

```bash
bid run
# or
bid resume
```

Inspect state with:

```bash
bid status
bid vc log
bid vc rollback <state>
```

## Tested Qwen3 llama.cpp CLI smoke

This bounded product smoke was run with Qwen3-4B-Instruct-2507 Q8_0 on an RTX
5070 Ti. It is a reproducible setup check, not a capability guarantee. Point
`MODEL` at a local copy of the GGUF; the recorded model hash, outcomes, and
runtime details are in the
[smoke report](evaluation/development-smoke/qwen3-4b-2026-11-02/REPORT.md).
The environment values below are smoke-only overrides; product defaults and
the generic quick start remain unchanged.

Start one llama.cpp server with the tested context and Q8_0 KV cache:

```bash
MODEL=/path/to/Qwen3-4B-Instruct-2507-Q8_0.gguf
llama-server --model "$MODEL" --host 127.0.0.1 --port 8081 \
  --alias Qwen3-4B-Instruct-2507-Q8_0 \
  --ctx-size 65536 --kv-unified-per-slot 65536 --parallel 1 \
  --n-gpu-layers 99 --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --no-context-shift --n-predict 8192 --jinja --seed 20261124
```

In another terminal, point BID at that server and run the exact-file smoke:

```bash
export BID_MODEL_ENDPOINT=http://127.0.0.1:8081/v1/chat/completions
export BID_MODEL_NAME=Qwen3-4B-Instruct-2507-Q8_0
export BID_WORKSPACE=./qwen-smoke
export BID_MAX_TOKENS=2048
export BID_REQUEST_TIMEOUT=20
export BID_WORKER_TIMEOUT=45
export BID_INACTIVITY_TIMEOUT=30
export BID_REPEAT_ACTION_LIMIT=3
export BID_PROVISIONAL_BATCH=1
export BID_MAX_TASK_REWORKS=1
export BID_MAX_MANAGER_REWORKS=1
export BID_PROTOCOL_GRAMMAR=0
export BID_MAX_SEARCHES=0
unset BID_ALLOW_HOST_RUN

bid init 'Create hello.txt containing exactly the single line `hello from BID`, with one final newline. Do not create any other files.'
bid run
```

The earlier Qwen runs used the source manifests listed in the
[smoke report](evaluation/development-smoke/qwen3-4b-2026-11-02/REPORT.md).
Primary later verified the corrected source through an ordinary Qwen CLI run
with a two-file arithmetic task and isolated generated-code checks. See the
[verification record](evaluation/development-smoke/primary-verification-2026-11-02/README.md)
and its source manifest. This bounded result is not a general capability or
architecture claim.

## Execution model

### 1. Manager planning

`bid init` creates the BID workspace, records the user request, initializes version control, and asks the Manager to produce a sequential Markdown checklist in `docs/todo.md`.

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

When `BID_REPO_CONTEXT=tools` or `inject`, the text protocol additionally
supports `MAP` or `MAP <relative-directory>` and `FIND <fixed literal>`. These
commands are read-only. `FIND` is fixed-literal, not regex, and reports exact
`path:line` coordinates over readable indexed UTF-8 text only.

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

Two harness checks sit around the Reviewer's judgement:

- If the last test command the Worker ran executed zero tests (for example `collected 0 items`), an ACCEPT is turned into a REWORK, because that run verified nothing.
- When a candidate has no diff (a verification-only task), the Reviewer is also shown the recorded `RUN` commands and output, and when a candidate changes files that already-ratified tasks wrote, the Reviewer is told which ones.

When a task uses up `BID_MAX_TASK_REWORKS`, BID does not stop. It restores the fixed base and asks the Manager to re-plan: the Manager sees the task, the Reviewer's last reason and the rejected diff, and must change the plan (`# Replace Remaining Plan` or `# Add`). Each task gets at most `BID_MAX_MANAGER_REWORKS` such escalations; after that BID stops with an error.

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

Normal task transitions append durable events to the VC log. Manual `vc rollback` is the explicit exception: it truncates later log entries and deletes later snapshots. The CLI warns before doing so; do not use manual rollback when you need to preserve forensic history. The transition records include:

```text
provisional: task=Tn base=sX candidate=sY review=ACCEPT
ratified:    task=Tn base=sX candidate=sY
invalidate:  task=Tn base=sX candidate=sY reason=...
```

The VC log is authoritative for provisional identity. On restart, BID replays an interrupted Manager reconciliation intent, reconciles `docs/todo.md` with the persisted log, repairs interrupted admission state when the durable record is present, and validates that referenced base/candidate snapshots still exist before continuing.

Workspace snapshots are retained for candidate submission, reconciliation, rollback, and replay.

An append-only JSONL observability log records model requests, commands, timings, token usage, transitions, candidate submission, rollback, and reconciliation events.

## Experimental repository context

This is an explicitly ablatable process optimization, not a presumed
correctness mechanism:

- `off`, empty, and `0` preserve the current Worker command and READ behavior.
- `tools` enables deterministic indexing, freshness, MAP, FIND, and duplicate
  READ suppression, with only a short capability notice and no injected map.
- `inject` enables `tools` plus a bounded factual map at startup and after a
  Worker soft reset. `1` is an alias for `inject`.

Unknown non-empty mode values are rejected. The index is local, versioned, and
atomically stored under `.bid/repo_context/`. It includes file paths and
classifications while searchable entries are regular UTF-8 text files within
the configured byte limit. Binary, oversized, unreadable, symlink, BID
control, and common metadata/cache paths are not searched. Multiple matches on
one line count as non-overlapping literal occurrences. Output is bounded by
the configured map, hit, and file-size limits.

Repeated unchanged text-protocol READs in one Worker history epoch return a
short SHA-256 notice; changed files and post-reset reads return full content.
The in-memory ledger is not shared across Worker sessions.

This is file-level Tier 1 orientation, not a symbol graph, call graph,
embedding, semantic summary, or impact tracer. Expected direct effects are
reduced repository rediscovery, repeated reads, tool calls, latency, or
context consumption; correctness effects are unknown. Generic context
injection has not been shown to reliably improve coding-agent pass rates.
The three modes exist for controlled ablation against the current BID
baseline; no performance or correctness improvement is claimed.

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

`RUN` is **disabled by default**. Set `BID_ALLOW_HOST_RUN=1` only when you trust the task, model output, and every executable it may invoke. This explicitly allows arbitrary code to run with the BID process user's host privileges and network access; it is not a sandbox. Commands are argv-parsed (not sent through a shell), direct deletion commands are checked, and protected control files are restored if detected changed, but those checks do not contain subprocesses or prevent other host-side effects.

## Progress control

Worker sessions are bounded by several deterministic controls:

- Worker session deadline, passed as the remaining timeout to built-in model HTTP requests and RUN subprocesses. HTTP client timeouts are per-operation phase limits rather than hard cancellation; custom backends that ignore timeout are checked only after returning
- inactivity timeout
- per-`RUN` timeout
- repeated-action stall detection with soft resets
- maximum web searches per Worker
- maximum Task Reviewer reworks per task
- maximum Manager-directed reworks per task
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
| `BID_WORKER_TIMEOUT` | `3600` | Cooperative Worker deadline, seconds; checked between operations |
| `BID_INACTIVITY_TIMEOUT` | `600` | Worker inactivity timeout, seconds |
| `BID_RUN_TIMEOUT` | `60` | Per-command `RUN` timeout, seconds |
| `BID_ALLOW_HOST_RUN` | unset | Set to `1` to allow trusted code to run with host-user privileges |
| `BID_REPEAT_ACTION_LIMIT` | `5` | Repeated-action threshold before reset |
| `BID_MAX_SEARCHES` | `10` | Search attempts per Worker |
| `BID_SEARCH_ENDPOINT` | empty | Optional search provider endpoint |
| `BID_PROVISIONAL_BATCH` | `4` | Provisional tasks before Manager reconciliation |
| `BID_MAX_TASK_REWORKS` | `3` | Task Reviewer reworks allowed per task before it is escalated to the Manager |
| `BID_MAX_MANAGER_REWORKS` | `3` | Manager-directed reworks, and escalations of a task to the Manager, allowed per task |
| `BID_WORKER_CONTINUITY` | `reset` | `reset` gives every task a fresh Worker conversation; `continuous` carries prior successful-task conversation (experimental ablation) |
| `BID_REPO_CONTEXT` | `0` | `off`, `tools`, or `inject`; `1` aliases `inject` (experimental) |
| `BID_REPO_CONTEXT_MAX_CHARS` | `12000` | Bound for maps and injected orientation |
| `BID_REPO_CONTEXT_MAX_FIND_HITS` | `100` | Maximum emitted literal-search hits |
| `BID_REPO_CONTEXT_MAX_FINDS` | `32` | FIND commands allowed per Worker before closure |
| `BID_REPO_CONTEXT_FIND_CLOSURE_MAX_CHARS` | `8000` | Bound for the deterministic FIND closure evidence |
| `BID_REPO_CONTEXT_MAX_FILE_BYTES` | `1048576` | Maximum UTF-8 file size indexed for search |
| `BID_BACKEND` | unset | Set to `mock` for the built-in mock backend |
| `BID_PROTOCOL_GRAMMAR` | unset | Set to `1` to send role-specific syntax-only GBNF constraints to compatible llama.cpp endpoints; semantic review and completion decisions remain with the model |
| `BID_SEARCH_MOCK` | unset | Set to `1` for the mock search provider |

## Project layout

- `bid/harness.py` — orchestration, task lifecycle, reconciliation, persistence recovery
- `bid/adapter.py` — Manager/Worker/Reviewer adapters and Worker command execution
- `bid/vc.py` — workspace snapshots and VC event history
- `bid/todo.py` — canonical task parsing and state transitions
- `bid/permissions.py` — workspace path and role permissions
- `bid/protocol.py` — single source for the Worker command protocol
- `bid/repo_context.py` — optional file-level repository index, MAP/FIND, READ accounting
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
