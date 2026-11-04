# Runtime preparation evidence

Host: remote WSL2 Ubuntu 22.04.5, kernel `6.6.87.2-microsoft-standard-WSL2`,
user `iclab` (uid 1000), 20 vCPU, 98 GiB RAM, 73 GiB free after the 20 images.
`systemctl` is offline because WSL does not run systemd as PID 1.

## Packages and provenance

Docker packages came from the already-configured Docker CE apt repo
(`https://download.docker.com/linux/ubuntu jammy stable`) with
`/etc/apt/keyrings/docker.asc`, OpenPGP fingerprint
`9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88`. Installed with
`--no-install-recommends`:

| package | version |
| --- | --- |
| docker-ce | `5:29.9.2-1~ubuntu.22.04~jammy` |
| docker-ce-cli | `5:29.9.2-1~ubuntu.22.04~jammy` |
| containerd.io | `2.4.2-3~ubuntu.22.04~jammy` |
| docker-ce-rootless-extras | `5:29.9.2-1~ubuntu.22.04~jammy` |
| docker-buildx-plugin | `0.38.1-1~ubuntu.22.04~jammy` |
| uidmap | `1:4.8.1-2ubuntu2.2` |
| slirp4netns | `1.0.1-2` |
| fuse-overlayfs | `1.7.1-1` |
| dbus-user-session | `1.12.20-2ubuntu4.1` |

`uidmap`, `slirp4netns`, and `fuse-overlayfs` came from the signed Ubuntu
archive (jammy/jammy-updates). `rootlesskit 3.3.0` is provided by
`docker-ce-rootless-extras`; Ubuntu's conflicting `rootlesskit` package was not
installed. No `curl | sh` was used.

## Service state

- Rootless daemon runs as `iclab`; socket `~/.run/docker.sock`; data root
  `~/.local/share/docker`; no `/var/run/docker.sock` and no rootful
  `containerd` socket. `iclab` is not in the `docker` group.
- Every controller command must export
  `DOCKER_HOST=unix:///home/iclab/.run/docker.sock` (and
  `XDG_RUNTIME_DIR=/home/iclab/.run` for `dockerd-rootless.sh`); the default
  `/var/run/docker.sock` intentionally does not exist.
- Rootful `docker.service`, `docker.socket`, and `containerd.service` are
  masked to `/dev/null` so a later systemd enable cannot expose them.
- Start/stop: `runtime/dockerd-rootless-start.sh`,
  `runtime/dockerd-rootless-stop.sh`, launched detached with `setsid -f`.
  `DOCKERD_ROOTLESS_ROOTLESSKIT_DETACH_NETNS=false` is required here: the
  default detached mode left the slirp4netns helper defunct after the SSH
  session ended, so containers lost DNS and egress. Non-detached mode keeps
  the daemon and bridge in the slirp namespace and survives reconnects.
- `--disable-host-loopback` remains on. Agent task containers use
  `--network none`; they do not reach host loopback, the WAN, or other host
  services. BID/Worker run on the controller host and reach the proxy there.

## Validated behavior

- `docker pull alpine:3.20`; `docker run --rm` writes to overlayfs; user
  namespace is unshared; DNS resolution and HTTPS egress work.
- `docker run --rm --cap-add SYS_ADMIN` works (the official grader needs it for
  browser tests); agent cells do **not** receive SYS_ADMIN.
- `docker buildx build --load` works through the daemon BuildKit; the first
  SWE-bench image (`sympy__sympy-20590`, 4.07 GB) built in 171 s.
- The daemon and a newly started container still work from a separate SSH
  connection after the starting session closes.

## Known limits

- Cgroup driver is `none`: without systemd delegation, rootless Docker gets no
  per-container CPU/memory limits. Namespace, mount, network, seccomp, and
  capability isolation are intact. SWE-bench cells rely on the controller's
  wall/token budget, not cgroup limits.
- AppArmor is disabled by this WSL kernel (`/sys/module/apparmor/parameters/
  enabled` = `N`). Nothing was disabled or changed; do not claim AppArmor
  confinement.
- No systemd user service exists. The scripts are the service.

## Model egress and controller boundary

The model server stays on `127.0.0.1:8081`. BID/Worker and mini run on the
controller host; only the policy process talks to `proxy.py` on loopback. Each
BID/Worker `RUN` is a fresh `--network none` container with only the project
mounted at `/testbed`; mini's persistent container is also `--network none`.
No agent code can reach the model server, WAN, host IP services, or the Docker
socket. `RUN` containers are removed after success/timeout, so background
descendants cannot mutate the project after host bookkeeping resumes. BID's
`.bid/` and exact `docs/*` control files are inside the project and visible to
RUN; BID snapshots/restores control state around RUN and the export exclusion
removes them. They are not claimed hidden.

`proxy.py` performs llama.cpp `/apply-template` + `/tokenize` preflight, reserves
prompt+output atomically, clamps output to `min(4096, remaining, context)`,
fails closed on missing/invalid usage, forces the frozen sampling/seed/model,
and writes a JSONL usage log plus a bounded request/response trace outside the
agent mount. It writes a monotonic deadline at the first request; a common
host watchdog kills the runner process group and every owned per-RUN/mini
container at that deadline, even with no further model calls. Setup duration
is recorded separately. A preflight failure logs no stale usage (`usage_known`
false).

## Unscored synthetic integration

All three arms were exercised end to end on a synthetic repo, using the official
`sympy__sympy-20590` task image and a deterministic fake model upstream:

- BID: Manager → Worker `RUN` + `WRITE` → Task Reviewer `ACCEPT` → Manager
  reconciliation `COMPLETE`; each RUN used a fresh project-only `/testbed`
  container; `core.py` changed; proxy roles `manager`, `worker`, `reviewer`,
  `manager_reconcile`.
- Per-RUN isolation: a background `setsid` delayed write did not survive the
  container; a timed-out RUN left no marker; project writes persisted.
- Shared deadline: with a fake first request and a long tool command and no
  further HTTP, both BID and mini were killed at a short deadline; no delayed
  marker appeared; setup and deadline records were written.
- Worker control: single Worker `WRITE`; artifact changed; no Manager/Reviewer.
- mini: installed 2.4.6 entrypoint, Docker environment, two tool calls,
  `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`; artifact changed.
- Negative boundary: a per-RUN container had no Docker socket, no `/opt/bid`,
  no task/grading file, only the project at `/testbed`, no DNS/WAN;
  `python`/`git` present; a malicious git clean/smudge filter did not create
  the host marker during containerized patch extraction.
- Control-path preflight across all 20 base commits: no `.bid`, `.bid_backup`,
  or BID `docs/*` control path exists at base, so the export exclusion cannot
  erase legitimate repo docs.
- Agent-runtime preflight across all 20 images: `python -V` succeeds in every
  image; `git` and `python` are present in the official task image used for
  synthetic integration.
- Patch extraction for all three came from the no-network extraction container,
  not host git. No scored 20-issue inference was run.
- Real Qwen smoke (unscored): started the pinned Qwen3-4B Q8_0 / llama.cpp
  server on `127.0.0.1:8081`, verified actual `/apply-template` and `/tokenize`,
  and sent one request through `proxy.py` (prompt estimate 13 = actual 13,
  completion 2, seed 7, max_tokens 8, status 200). A Worker-only synthetic cell
  then produced a real `answer.py` patch (two requests, 615 and 644 logical
  tokens) through a per-RUN offline container. The server/proxy were stopped;
  no listeners remain.
- Broad suite: `pytest tests evaluation/swebench-pilot` → 222 passed, 11
  skipped locally; 233 passed on Linux test venv. `pilot.py --dry-run` lists
  60 cells in the frozen order; `--instance <first> --limit 3` lists the
  three-arm canary.

## Condition runtimes

- Evaluator venv: `swebench 5.0.2` installed editable from the pinned source,
  `docker 7.2.0`, `datasets 5.1.0`.
- Host BID runtime: benchmark venv with `httpx 0.28.1`; `import bid, httpx`
  verified before any cell clock. BID/Worker run here; they never execute
  agent commands on the host.
- mini venv: `mini-swe-agent 2.4.6` installed editable from
  `source/mini-swe-agent`, plus `mcp 2.3.0`; `mini --help` and actual entrypoint
  verified.
- mini 2.4.6 uses a per-cell `MSWEA_GLOBAL_CONFIG_DIR` and
  `MSWEA_CONFIGURED=true` so it cannot block on interactive first-run setup.

## Task images

All 20 frozen instances have an image built from the pinned task repo with
`swebench images build ... -n swebench -j 4` (31m37s, 20 succeeded, 0 failed).
Every expected `swebench/sweb.eval.x86_64.*_1776_*:latest` tag passed
`docker image inspect`; 21 `swebench/` images exist including the sanity
image, and 73 GiB remained free. No scored model inference has run.

## Cohort controller

`pilot.py` validates the manifest/ID/public-input hashes, hidden grading hash,
and live BID/runner/prompt source hash, rebuilds the frozen 60-cell order, runs
one condition at a time, grades each prediction with a unique official run ID,
writes atomic `complete.json` checkpoints + JSONL/CSV outcomes, records source
identity and disk free, refuses to rerun completed cells, and stops an owned
server in `finally`. It refuses execution without `--execute`; `--dry-run`
lists the frozen queue. A disk floor (default 10 GiB) stops the cohort before
exhaustion. `--instance <id>` filters all three arms; `--limit 3` prepares the
first-issue canary.

Recommended controller invocation from the source snapshot:

```
cd /home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/bid-source/evaluation/swebench-pilot
PYTHONPATH=/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/bid-source \
  /home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/venv/bin/python pilot.py \
  --manifest /home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/manifest.json \
  --public /home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/frozen/task-inputs.jsonl \
  --grading-rows /home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/frozen/grading/instances.jsonl \
  --source-root /home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/bid-source \
  --controller-root /home/iclab/bid-benchmarks/swebench-verified20-2026-11-02 \
  --output /home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/runs \
  --repo-cache /home/iclab/.run/repo-mirrors \
  --mini-python /home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/mini-venv/bin/python \
  --server-pid-file /home/iclab/bid-llama-runtime/server.pid \
  --server-identity-file /home/iclab/bid-llama-runtime/server.identity.json \
  --server-binary /home/iclab/bid-llama-runtime/src/llama.cpp/build/bin/llama-server \
  --server-model /home/iclab/bid-models/Qwen_Qwen3-4B-Instruct-2507-Q8_0.gguf \
  --instance django__django-10097 --limit 3 --execute
```

The stop helper fails closed without a matching PID/start-time identity and
exact binary/model/`127.0.0.1`/port check; it never SIGKILLs and records whether
the process actually stopped. The identity file is JSON
`{"pid": <pid>, "starttime_ticks": <field 22 of /proc/<pid>/stat>}`.

## Official grader sanity

Commands ran from the benchmark controller with
`DOCKER_HOST=unix:///home/iclab/.run/docker.sock` and the pinned evaluator:

```
python -m swebench.harness.run_evaluation \
  --dataset_name frozen/grading/sanity.jsonl \
  --predictions_path gold \
  --run_id sanity-gold-20261102-a1 \
  --instance_ids sympy__sympy-20590 --max_workers 1
```

- `sanity-gold-20261102-a1`: 1 submitted, 1 completed, 1 resolved, 0
  unresolved, 0 infra/error/empty-patch; `patch_successfully_applied=true`;
  FAIL_TO_PASS `test_immutable` passed; 21/21 PASS_TO_PASS passed.
- `sanity-base-20261102-a1`: sentinel empty-file patch, 1 completed, 0
  resolved, 1 unresolved, 0 infra/error/empty-patch;
  `patch_successfully_applied=true`; FAIL_TO_PASS `test_immutable` failed;
  all 21 PASS_TO_PASS passed.

Reports: `logs/evaluation/<run_id>/results.json` and
`logs/evaluation/<run_id>/<model>/sympy__sympy-20590/report.json`.
SHA-256: gold results `21ce17ca478679292a6bbd9d1c6aaf29b5f2efdd80e9d5a6bd153514c3dd55f5`,
gold report `8b9789cc302b46bed7edeaed0ecc1e0f8af15c9263c46131c34440347f809717`,
base results `931f25d8568b8d07ff6e73670cee271212b7635420931d5aa46205106ff4585b`,
base report `9a1de1ccb578c95a24049843f05c9db0904b9b05d3aeccec7381799007be411b`.
The base patch is the 93-byte empty-file sentinel; both patches applied.
Run IDs are unique; no caching collision. This is not a scored run and the
sanity instance is outside the frozen 20.
