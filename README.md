# Breaking It Down (BID)

BID is a local-first harness for long-horizon work with small language models. It keeps project state in files, breaks the request into sequential tasks, and gives each disposable Worker only one task.

The harness owns procedure. The model reads, writes, searches when available, reasons about its assigned task, and produces work.

## Runtime model

```text
User task
→ Manager creates docs/todo.md checklist
→ BID starts the first unchecked Worker
→ Worker reads/writes/searches for one task
→ Worker outputs Done
→ BID saves a candidate VC state
→ Reviewer compares base to candidate
→ ACCEPT marks the task checked
→ next Worker or finish
```

A checkbox means **accepted**.

BID observes the filesystem, TODO transitions, model output, repeated operations, inactivity, and hard runtime ceilings. The Worker is not required to call an invented `finish`, `submit_task`, or `check_own_task` tool.

## Authority

- **Manager:** creates and revises the plan, reviews candidate diffs, reopens tasks, and declares the project complete.
- **Worker N:** performs only Task TN and may change any non-control workspace files.
- **Harness:** selects roles, validates writes, detects progress and loops, handles timeouts, saves candidate states, reviews diffs, and marks accepted tasks.
- **VC:** native linear snapshots named `s0`, `s1`, `s2`, and so on.

## Model operations

The current local tool surface is deliberately small:

- `list_files`
- `read_file`
- `write_file`
- `replace_text`

There are no Worker terminal tools. Completion is observable state:

```text
 worker Done + reviewer ACCEPT → normal completion
 worker Done + reviewer REWORK → task stays open for another pass
 timeout/crash/stall → rollback
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

Each submitted worker run produces one candidate state. Manual rollback remains available.

## Tests

```bash
python -m pytest -q
```

The suite covers TODO parsing, permissions, worker submission, retry/rework, diff review, and the Manager → Worker → Review lifecycle.

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
