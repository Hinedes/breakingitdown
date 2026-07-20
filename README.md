# Breaking It Down (BID)

BID is a local-first harness for long-horizon work with small language models. It keeps project state in files, breaks the request into sequential tasks, and gives each disposable Worker only one task.

The harness owns procedure. The model reads, writes, searches when available, reasons about its assigned task, and produces work.

## Runtime model

```text
User task
→ Manager creates docs/todo.md
→ BID starts the first unchecked Worker
→ Worker reads/writes one task
→ Worker checks only its own TODO box
→ Worker may continue revising or backtrack
→ Worker outputs Done
→ BID records a VC state
→ next Worker
→ Manager reviews all submitted work
```

A checkbox means **submitted for Manager review**, not accepted.

BID observes the filesystem, TODO transitions, model output, repeated operations, inactivity, and hard runtime ceilings. The Worker is not required to call an invented `finish`, `submit_task`, or `check_own_task` tool.

## Authority

- **Manager:** creates and revises the plan, reviews artifacts, reopens tasks, and declares the project complete.
- **Worker N:** performs only Task TN and may change only its own checkbox in `docs/todo.md`.
- **Harness:** selects roles, validates writes, detects progress and loops, handles timeouts, saves states, rolls back failed work, and schedules the next role.
- **VC:** native linear snapshots named `s0`, `s1`, `s2`, and so on.

## Model operations

The current local tool surface is deliberately small:

- `list_files`
- `read_file`
- `write_file`
- `replace_text`

There are no Worker terminal tools. Completion is observable state:

```text
own checkbox checked + final output Done → normal submission
own checkbox checked + timeout/crash/stall → abnormal submission for Manager review
own checkbox unchecked + timeout/crash/stall → rollback
```

## Quick start

```bash
python -m pip install -e '.[test]'

./llama-server -m smollm3-3b-q4_k_m.gguf --port 8080

python bid.py init "YOUR TASK"
python bid.py run
python bid.py status
```

Mock backend:

```bash
BID_BACKEND=mock python bid.py init "YOUR TASK"
BID_BACKEND=mock python bid.py run
```

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `BID_MODEL_ENDPOINT` | `http://127.0.0.1:8080/v1/chat/completions` | OpenAI-compatible local endpoint |
| `BID_MODEL_NAME` | `smollm3-3b` | Model identifier |
| `BID_MAX_TOKENS` | `8192` | Per-response allowance; Workers are not silently restricted |
| `BID_REQUEST_TIMEOUT` | `300` | Maximum time for one backend request |
| `BID_INACTIVITY_TIMEOUT` | `600` | Maximum period without useful activity |
| `BID_WORKER_TIMEOUT` | `3600` | Hard ceiling for one role session |
| `BID_REPEAT_ACTION_LIMIT` | `5` | Identical no-progress operations before declaring a stall |
| `BID_WORKSPACE` | `./workspace` | Shared project tree |
| `BID_TEXT_TOOLS` | `1` | Text-encoded tools for models without native tool calls |
| `BID_BACKEND` | unset | Set to `mock` for deterministic tests |

SmolLM3 requests explicitly disable extended thinking through the llama.cpp chat-template parameter. Detailed role policy lives in `docs/manager.md` and `docs/worker.md`; bootstrap prompts remain short.

## Native VC

```bash
python bid.py vc log
python bid.py vc rollback s3
```

Each accepted role run produces one complete named state. Unfinished unchecked work is restored to the previous state.

## Tests

```bash
python -m pytest -q
```

The suite covers TODO parsing and ownership, role permissions, tool execution, observer state, repeated-action stalls, Worker backtracking, abnormal checked submission, rollback, Manager review, and the complete Manager → Workers → Manager lifecycle.

## Project tree

```text
bid.py
bid/
├── cli.py
├── harness.py
├── model.py
├── observer.py
├── permissions.py
├── session.py
├── todo.py
├── tools.py
└── vc.py
prompts/
tests/
workspace/
```
