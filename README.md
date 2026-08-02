# BID — Break It Down

A local-first agent harness that breaks a task into a Markdown checklist,
assigns each step to a disposable Worker, validates results through
a Task Reviewer, and lets a returning Manager reconcile provisional
submissions into permanently DONE work.

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
| `BID_PROVISIONAL_BATCH` | `4` | Provisional submissions before Manager reconciliation |
| `BID_MAX_TASK_REWORKS` | `3` | Task Reviewer REWORKs per task before BID stops with a terminal error |

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

TODO states:

- `[ ]` — unchecked, executable
- `[-]` — provisional (Task Reviewer admitted the candidate; awaiting Manager)
- `[x]` — DONE (Manager-ratified; only reconciliation may produce this)

1. **Manager** reads the task and produces a sequential checklist in
   `docs/todo.md`.
2. **Worker** processes one unchecked task using READ/RUN/WRITE/REPLACE/Done.
3. **Task Reviewer** judges the candidate against the fixed-base→candidate diff.
   If rejected, the workspace rolls back to the task's fixed base. If accepted,
   the candidate becomes **provisional** (`[-]`) — never DONE.
4. When the provisional count reaches `BID_PROVISIONAL_BATCH` (default 4) or no
   unchecked task remains, **Manager reconciliation** reviews every provisional
   submission (description, fixed base, candidate, diff, RUN evidence, ACCEPT
   verdict) and returns one batch decision: `# Done`, `# Rework` (with reason),
   `# Add`, `# Replace Remaining Plan` (unchecked tasks only), and a mandatory
   `# Project` (`CONTINUE`/`COMPLETE`).
5. The harness validates the complete decision before any mutation, then
   applies it: ratification (`[x]`), suffix invalidation on REWORK (restore to
   the task's fixed base, uncheck the suffix, invalidate its records), plan
   additions/replacement, and termination.
6. A Worker submission remains provisional until a returning Manager
   reconciliation converts it to permanently DONE work. Only the Manager
   declares `COMPLETE`.

Provisional identity is durable across restarts: the append-only VC log
records `provisional:`, `ratified:`, and `invalidate:` entries per task; the
TODO marker is repaired from the log on admission interruption, and
inconsistent TODO/log state fails closed.

Workspace snapshots are preserved at every candidate submission for forensic
replay. An append-only JSONL event log captures timing, usage, and transitions.

## Known Limitations

- **No automatic repository map or specialized code-search helper.** Workers
  may discover files through RUN commands such as `ls`, `find`, and `grep`,
  or receive paths in the task description.
- **No fuzzy or line-based editing.** REPLACE requires exact text matching.
- **No parallelism.** Only one Worker runs at a time.
- **No candidate salvage.** Rejected work is preserved for analysis but not
  recovered automatically into subsequent attempts.
- **No GUI.** Command-line only.
- **Semantic acceptance is model-judged and may be wrong.** Diffs, tests,
  and RUN results provide evidence but do not independently determine
  semantic correctness.
- **No automatic harness-owned verification pipeline.** Workers may run tests
  through RUN commands, but the harness does not automatically execute or
  validate them.
- **Manager reconciliation may extend the checklist** when it judges the
  original request incomplete, potentially adding tasks beyond the initial
  plan; a REWORK invalidates the reworked task and every later provisional
  task, restoring the workspace to the reworked task's fixed base.
- **Validated with the automated test suite, a deterministic CLI smoke
  workflow, and one substantial but incomplete ARGUS evaluation.** General
  performance across unrelated repositories remains unproven.

## Possible Future Work

- Repository navigation helpers (search, directory maps)
- Fuzzy or line-range editing
- Built-in verification commands
- GUI/web interface
- Additional benchmark validation

## Out of Scope Unless Evidence Justifies Them

- Worker parallelism and inter-Worker graphs
- Candidate salvage and recombination

