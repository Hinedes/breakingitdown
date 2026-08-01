# BID — Break It Down

A local-first agent harness that breaks a task into a Markdown checklist,
assigns each step to a disposable Worker, and validates results through
a Task Reviewer before continuing.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+ and `httpx>=0.27`.

## Quick Start

```bash
export BID_MODEL_ENDPOINT="http://127.0.0.1:8080/v1/chat/completions"
export BID_MODEL_NAME="your-model"
export BID_WORKSPACE="./my-task"

bid.py init "Your task description here"
bid.py run
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `BID_MODEL_ENDPOINT` | `http://127.0.0.1:8080/v1/chat/completions` | Model API endpoint |
| `BID_MODEL_NAME` | `smollm3-3b` | Model name |
| `BID_WORKSPACE` | `./workspace` | Project directory |
| `BID_MAX_TOKENS` | `32768` | Max tokens per request |
| `BID_WORKER_TIMEOUT` | `3600` | Hard ceiling per Worker (seconds) |
| `BID_REQUEST_TIMEOUT` | `300` | HTTP request timeout |
| `BID_INACTIVITY_TIMEOUT` | `600` | Inactivity boundary |
| `BID_RUN_TIMEOUT` | `60` | Per-`RUN` command timeout |
| `BID_REPEAT_ACTION_LIMIT` | `5` | Repeat stall threshold |

## Worker Protocol

Workers control the workspace through a text command protocol:

```
READ <path>                 — read a UTF-8 file
RUN <program> [args...]     — execute a command
WRITE <path>                — overwrite a file
<content>
END WRITE
REPLACE <path>              — exact-text substitution
<old text>
---REPLACE_WITH---
<new text>
END REPLACE
Done                        — submit current candidate
```

REPLACE requires exactly one match; zero or multiple matches fail without
modifying the file. The old text must be copied verbatim from a recent
READ, preserving indentation.

## Architecture

1. **Manager** reads the task and produces a sequential checklist in
   `docs/todo.md`.
2. **Worker** processes one unchecked task using READ/RUN/WRITE/REPLACE/Done.
3. **Task Reviewer** judges the candidate against the fixed-base→candidate diff.
4. If rejected, the workspace rolls back to the task's fixed base.
5. If accepted, the checklist advances to the next task.
6. **Completion Reviewer** decides whether the final workspace satisfies the
   original request.

Workspace snapshots are preserved at every candidate submission for forensic
replay. An append-only JSONL event log captures timing, usage, and transitions.

## Known Limitations

- **No repository navigation assistance.** The Worker must be told which files
  to edit in the task description.
- **No fuzzy or line-based editing.** REPLACE requires exact text matching.
- **No parallelism.** Only one Worker runs at a time.
- **No candidate salvage.** Rejected work is preserved for analysis but not
  recovered automatically into subsequent attempts.
- **No GUI.** Command-line only.
- **REVIEWER model must judge correctly.** The harness holds no acceptance
  oracle.
- **This release has only been tested with one ARGUS benchmark.** General
  task performance varies.

## Deferred

- Repository search/navigation assistance
- Fuzzy or line-range editing
- Worker parallelism and graphs
- Candidate salvage and recombination
- Built-in verification commands
- GUI/web interface
- Additional benchmark validation
