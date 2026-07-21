# Breaking It Down (BID)

BID is a local-first harness for long-horizon work with small language models. It decomposes a project into sequential single-task contexts, persists every accepted state, and keeps procedure in code rather than asking the model to operate the harness.

> Code owns I/O, scheduling, permissions, recovery, review dispatch, and version control. The model performs one bounded cognitive job at a time.

## Processor chain

```text
User task
→ ManagerInitAdapter creates the TODO
→ WorkerAdapter performs T1, T2, ... sequentially
→ ArtifactReviewAdapter reviews one artifact per fresh context
→ rejected tasks reopen and repair Workers run
→ CompletionReviewAdapter checks whole-project coverage
→ DONE or new missing tasks
```

A checked box means **submitted for Manager review**, not accepted.

## Model protocol

Workers use four plain-text operations. There is no JSON tool protocol and no invented terminal tool.

```text
READ docs/input.md

SEARCH current ROCm llama.cpp support

WRITE docs/work/T1.md
artifact content
END WRITE

Done
```

`Done` terminates only when the Worker’s own checkbox is checked. A checked Worker may continue revising or uncheck the task before it finally says `Done`.

## Harness authority

- **Manager:** decomposes the request and judges project completeness.
- **Worker N:** performs only Task TN and may change only its own TODO checkbox.
- **Harness:** owns role dispatch, file access, search execution, cache storage, progress detection, timeouts, review scheduling, rollback, and recovery.
- **Native VC:** stores linear states `s0`, `s1`, `s2`, ... without Git.

## Search provenance

`SEARCH` is executed by BID, not by the model. Results are bounded, sanitized, attributed, and stored under:

```text
docs/research/TN/search-NNN.md
```

The directory is harness-write-only and Worker-read-only. Canonical queries use a persistent cache under `.bid/search_cache`; cache hits are materialized into the active task’s research directory. Research-backed artifacts must cite a stored evidence path or a URL present in that task’s evidence.

The built-in live provider currently uses DuckDuckGo’s Instant Answer endpoint. Tests inject `MockSearchProvider` explicitly.

## Recovery model

BID persists an active-session journal before every Worker or review phase.

```text
checked Worker + process death   → recover as abnormal submission
unchecked Worker + process death → restore the previous VC state
interrupted review               → restore the pre-review state
completed VC commit + stale marker → clear marker without duplicating state
```

Initialization uses a sibling journal and backup so an interrupted reinitialization restores the previous workspace. A stable workspace lock serializes `run`, `init`, and `vc rollback`.

## Quick start

```bash
python -m pip install -e '.[dev]'

./llama-server -m smollm3-3b-q4_k_m.gguf --port 8080

python bid.py init "YOUR TASK"
python bid.py run
python bid.py status
```

Mock model/search execution:

```bash
BID_BACKEND=mock BID_SEARCH_MOCK=1 python bid.py init "YOUR TASK"
BID_BACKEND=mock BID_SEARCH_MOCK=1 python bid.py run
```

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `BID_MODEL_ENDPOINT` | `http://127.0.0.1:8080/v1/chat/completions` | OpenAI-compatible local endpoint |
| `BID_MODEL_NAME` | `smollm3-3b` | Model identifier |
| `BID_MAX_TOKENS` | `8192` | Per-response allowance |
| `BID_REQUEST_TIMEOUT` | `300` | Maximum time for one backend request |
| `BID_INACTIVITY_TIMEOUT` | `600` | Maximum period without useful state progress |
| `BID_WORKER_TIMEOUT` | `3600` | Hard ceiling for one Worker session |
| `BID_REPEAT_ACTION_LIMIT` | `5` | No-progress turns before a soft reset |
| `BID_MAX_SEARCHES` | `10` | Maximum live provider requests per Worker; cache hits are free |
| `BID_SEARCH_ENDPOINT` | DuckDuckGo Instant Answer | Optional compatible endpoint override |
| `BID_WORKSPACE` | `./workspace` | Shared project tree |
| `BID_BACKEND` | unset | Set to `mock` for deterministic model tests |
| `BID_SEARCH_MOCK` | unset | Set to `1` only for mock search tests |

## Native VC

```bash
python bid.py vc log
python bid.py vc rollback s3
```

Snapshots and metadata updates are atomic. Rollback is serialized against active project execution and restores the previous live tree if an intermediate filesystem operation fails.

## Tests

```bash
python -m pytest -q
```

The same command runs in GitHub Actions on every push and pull request. The suite covers decomposition, sequential Workers, review-and-repair, completion, search provenance, cache isolation, permissions, no-progress recovery, abnormal submission, crash journals, transactional initialization, concurrency locks, and VC fault injection.

## Project tree

```text
bid.py
bid/
├── adapter.py
├── cli.py
├── harness.py
├── model.py
├── observer.py
├── permissions.py
├── search.py
├── session.py
├── todo.py
├── tools.py
└── vc.py
prompts/
tests/
workspace/
```
