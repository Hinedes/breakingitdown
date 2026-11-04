# Conditions and interfaces

Same 20 instances, base commits, public issue text, model, generation seed,
600 s wall budget, 180,000 logical prompt+completion tokens, 4,096 generated
tokens per request, 65,536-token context, one model server at a time, fresh
checkout per cell. The arms are not identical in tool interface.

## BID (full flow)

- Manager initializes a one-task-at-a-time checklist (`provisional_batch=1`);
  Workers execute; Task Reviewer accepts or reworks each candidate; Manager
  reconciles in batches of one.
- Worker commands: `READ`, `RUN`, `WRITE`, `REPLACE`, `Done`. `SEARCH` is
  disabled (`max_searches_per_worker=0`); grammar bias is explicitly off.
- The exact public `problem_statement` is injected into every Worker prompt
  (`task_statement` config), including BID's Manager-assigned Workers. It is
  the same public issue text the direct Worker receives; no gold/test/hints.
- BID runs on the controller host. Each `RUN` is a fresh no-network,
  `--cap-drop ALL` container with only the project at `/testbed`, removed after
  success/timeout; there is no persistent agent container and no host RUN.
- Candidate changes live in the checkout; `.bid/` and BID's exact `docs/*`
  control files are stripped before prediction export.

## Worker only (matched)

- One `WorkerAdapter` on a one-task TODO, no Manager, no Task Reviewer, no
  reconciliation.
- Same Worker command grammar, exact public issue injection, model, seed,
  budget, fresh checkout, host policy, and task-container RUN path as BID.
  This isolates role organization, not tool vocabulary or information access.

## mini-SWE-agent (stock policy)

- Pinned `SWE-agent/mini-swe-agent` commit `04d809ce...`; stock `mini.yaml`
  policy with `-y --exit-immediately`, Docker environment, network `none`.
- Interface is bash tool calls, not BID commands; no Reviewer, no Manager.
  `step_limit` remains 0; the proxy's shared 600 s/180k budget is the stop rule.
- Per-cell `mini_pilot.yaml` adds only runtime overrides: Docker environment,
  instance image, checkout mount, network `none`, `cost_tracking:
  ignore_errors`, and frozen sampling: `temperature=0.6`, `top_p=0.95`,
  `top_k=20`, `min_p=0`, `presence_penalty=0`, `repetition_penalty=1`,
  `max_tokens=4096`, per-issue `seed`. The proxy also forces these values.
- Mini runs on the controller host; its model calls go to the controller proxy.
  Its task container has no network, no Docker socket, and no controller data.

## Shared controller rules

- Agent-visible input is exactly `instance_id`, `repo`, `base_commit`,
  `problem_statement`; hidden grading rows and reference patches are never in
  an agent-visible mount.
- Task containers mount only the project at `/testbed`. No BID source, task
  JSON, grading directory, proxy trace/log, controller config, credentials, or
  Docker socket. BID/Worker RUN containers are one-shot and removed before
  host metadata/patch reads; background descendants die with the container.
  `.bid/` and BID's `docs/*` control files are inside the project and therefore
  visible to RUN; BID's existing snapshot/restore guard runs around each RUN and
  the export exclusion removes those paths. This is stated as visible, not
  hidden harness state.
- Base checkouts are shallow and contain only the public base commit; origin,
  remote refs, reflogs, and future history are removed.
- Patch extraction runs in a separate container with `--network none`,
  `--cap-drop ALL`, read-only root, and only the project mounted. Host git is
  never run against a model-modifiable `.git`/`.gitattributes`.
- The controller proxy uses llama.cpp `/apply-template` and `/tokenize`
  preflight, atomic reservation, fail-closed missing/invalid usage, fixed
  sampling/seed/model, and an append-only JSONL log plus bounded request/
  response trace outside the agent mount. The wall clock starts at the first
  model request, after setup; one watchdog kills the runner and all owned
  containers at that deadline, even without further model calls.
- No lesson learning, cross-run memory, training, or grader-feedback retries
  in this pilot.

## Open before launch

- Primary prelaunch review of manifest, schedule, budgets, isolation, tool
  records, proxy, pilot checkpointing, and grader sanity evidence. All 20 task
  images are built and tag-verified; unscored synthetic/integration checks
  exercised all three arms and the shared deadline.
