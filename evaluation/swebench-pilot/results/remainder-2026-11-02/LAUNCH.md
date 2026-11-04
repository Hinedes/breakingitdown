# Remainder launch (57 cells) - prepared, launched by primary

Prepared 2026-11-02. This directory is the local export target for the
remainder run.

## Actual launch record (primary, main-session owned)

- uploaded launcher sha256 verified `027e90006d0279cce3008853b45356e6f82c250dca207fcad7a852048938213c`
- `--self-check`: 16 assertions passed
- `--preflight-only`: 60-cell queue, 57 missing checkpoints, source `f4999e…`,
  manifest `9e66…`, all 40 gate runs verified, `live_state_ok=true`, no process,
  container or port conflict, 72.26 GiB free
- launched in a main-session background shell; runtime
  `runtime/remainder-2026-11-02`, log `runtime/pilot.log`, approval snapshot
  `runtime/primary-approval.json`
- frozen policies carried in: 600 s wall / 180 000 logical tokens per cell,
  and the known BID `inactivity_timeout=120` which resets only on workspace file
  changes

Run outcome is not claimed here; see `PAUSE_DIAGNOSIS.md` for the pause and the
separate export for results.

## Launcher

- local file: `C:\Users\hind\AppData\Local\Temp\opencode\bid_swe_remainder.py`
- bytes / sha256: `25939` / `027e90006d0279cce3008853b45356e6f82c250dca207fcad7a852048938213c`
- TEMP file only. It lives outside the frozen source tree and is never imported
  by the product. The deployed pilot at `bid-source-f4999e9656a5` is used
  unchanged.

## Environment (set by the launcher, not inherited)

Before any production call the launcher replaces `os.environ` with exactly:

```
PATH=/usr/local/bin:/usr/bin:/bin
HOME=/home/iclab
PYTHONPATH=/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/bid-source-f4999e9656a5
PYTHONDONTWRITEBYTECODE=1
PYTHONUTF8=1
DOCKER_HOST=unix:///home/iclab/.run/docker.sock
XDG_RUNTIME_DIR=/home/iclab/.run
```

`pilot.run_one`, the grading subprocess, the container calls and the
llama-server `Popen` all inherit that mapping. Verified on the controller with
this exact env: `docker info` returns `29.9.2`, `ss` and `ps` work, the
`llama-server` binary resolves every shared library (including CUDA) and reports
its build, and the swebench/mini packages still import through their venvs.

## Live-state gate (before any model spawn)

In a real run, immediately before the server starts, the launcher fails closed if
any of these is true:

- an `llama-server`, pilot or proxy process is already running
- any Docker container exists
- `docker info` fails on the configured `DOCKER_HOST`
- port 8081 is already listening (catches a temporary stray model)
- free disk below 10 GiB

`--preflight-only` performs the same reads and records them but never starts a
model or a server.

## Supervision rules

- pauses only on infrastructure: grader non-zero/timeout, missing
  `completed_instances`, missing or non-zero `infra_failure_instances` /
  `error_instances`, checkpoint not completed, missing prediction, HTTP 5xx, an
  accepted response with unknown usage, or a 429 that is not one of the proxy's
  own budget/wall/context guards. An upstream model-server 429 (and the proxy's
  `actual usage exceeded the reservation/context`) still stops the cohort.
- ordinary model, protocol, wall-time and token-budget outcomes do not stop it.
- if server startup throws, the launcher still writes `final.json` and prints
  `FINAL_JSON` with `fatal` set, after stopping any server it managed to spawn.

## Commands (main session owns the run)

Upload to the versioned runtime location (once):

```powershell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\Users\hind\AppData\Local\Temp\opencode\bid_swe_remainder.py"))
ssh -o ConnectTimeout=20 -o BatchMode=yes user@100.122.138.59 "wsl -d Ubuntu-22.04 -u iclab -- bash -lc 'B=/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02; mkdir -p \$B/runtime/remainder-2026-11-02 && echo $b64 | base64 -d > \$B/runtime/remainder-2026-11-02/bid_swe_remainder.py && sha256sum \$B/runtime/remainder-2026-11-02/bid_swe_remainder.py'"
```

Expected remote sha256: `027e90006d0279cce3008853b45356e6f82c250dca207fcad7a852048938213c`

Synthetic supervision check (no controller state, no model):

```powershell
ssh -o ConnectTimeout=20 -o BatchMode=yes user@100.122.138.59 "wsl -d Ubuntu-22.04 -u iclab -- bash -lc 'B=/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02; \$B/venv/bin/python \$B/runtime/remainder-2026-11-02/bid_swe_remainder.py --self-check'"
```

Read-only preflight (writes only `runtime/remainder-2026-11-02/preflight.json`,
starts no server, runs no cell):

```powershell
ssh -o ConnectTimeout=20 -o BatchMode=yes user@100.122.138.59 "wsl -d Ubuntu-22.04 -u iclab -- bash -lc 'B=/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02; cd \$B && \$B/venv/bin/python \$B/runtime/remainder-2026-11-02/bid_swe_remainder.py --preflight-only'"
```

Full run - foreground inside a main-session background shell, stdout/stderr to the
runtime log, no `nohup`, no detached process:

```powershell
ssh -o ConnectTimeout=20 -o BatchMode=yes user@100.122.138.59 "wsl -d Ubuntu-22.04 -u iclab -- bash -lc 'set -o pipefail; B=/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02; cd \$B && \$B/venv/bin/python \$B/runtime/remainder-2026-11-02/bid_swe_remainder.py 2>&1 | tee \$B/runtime/remainder-2026-11-02/pilot.log'"
```

Last stdout line is a single compact JSON:

```
FINAL_JSON {"status": "...", "ran_cells": 57, "skipped_cells": 3, "checkpoints_present": 60, "paused": null, "fatal": null, "canary_checkpoints_unchanged": true, "server_stop": {...}, "runtime": "..."}
```

Exit code: `0` complete, `3` paused/fatal, `4` preflight or startup refusal.

## What it enforces before the first cell

- source `f4999e9656a553321af77c2111340ac80c6899be1afe84a1e0d708d31a828f23`
- manifest `9e662f1174352ffe349fd9e14ef0957aa8bee4308e3c502fcc56d572c995f0c0`
- unchanged public/grading row hashes and the 40-run gate record
- the 3 canary checkpoints byte-identical to the recorded snapshot (completed,
  graded, same source/manifest pins)
- exactly 57 missing checkpoint keys; the 3 canary cells are skipped, never rerun
- live-state gate above

## Effort envelope

Worst case per cell is 600 s agent + 1800 s grading + setup, so 57 cells are
guarded by a 48 h outer budget (the canary's 7200 s window is not reused).

## Verification status

- `py_compile`: OK
- `--help`: OK (lazy pilot import)
- `--self-check`: OK, 16 synthetic assertions (ordinary model failure, three
  proxy guard 429s, upstream 429, reservation-mismatch 429, HTTP 500, unknown
  usage, grader rc/timeout, missing `completed_instances`, infra and error
  counts, incomplete checkpoint, skipped checkpoint, missing prediction)
- `--preflight-only` on the controller: not executed (primary owns remote
  execution)
- no source, prompt, manifest, runner or model change; no git operation
