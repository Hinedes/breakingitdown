# Reproducibility fixture

`task.md` and `oracle.py` are a frozen smoke example, not any historical BID
evaluation task. Run the oracle against a disposable workspace with
`python oracle.py PATH`. It checks the required files, a collected-style test
function, and `greet("Ada")`; the generated module is executed, so use only
workspaces whose contents you trust.

For a repeatable BID-only smoke run, pin the model endpoint and immutable
model digest, then use these fixed per-request/session limits (the repository
does not implement an aggregate run-wide token cap):

```sh
export BID_WORKSPACE="$PWD/out/bid"
export BID_MODEL_ENDPOINT="http://127.0.0.1:8080/v1/chat/completions"
export BID_MODEL_NAME="<record immutable model digest>"
export BID_MAX_TOKENS=8192
export BID_REQUEST_TIMEOUT=60
export BID_WORKER_TIMEOUT=600
export BID_INACTIVITY_TIMEOUT=120
export BID_RUN_TIMEOUT=30
export BID_MAX_TASK_REWORKS=3
export BID_MAX_MANAGER_REWORKS=3
export BID_PROVISIONAL_BATCH=4
unset BID_ALLOW_HOST_RUN
bid init "$(cat evaluation/repro/task.md)"
bid run
python evaluation/repro/oracle.py "$BID_WORKSPACE"
```

`BID_MAX_TOKENS` is per request, not an aggregate budget. A paired run needs an
external supervisor to impose the same aggregate token and wall-clock ceilings
on both conditions and preserve those measurements.

For a paired run, make byte-identical fresh workspace copies from this fixture
and use the same model digest, endpoint/runtime, payload settings, total token
budget, wall-clock budget, and oracle. Save both condition implementations,
prompts, tool definitions, launch commands, exact environment, outputs, and
oracle results. A custom single-worker Direct runner is only that runner; do
not label it a native coding agent or representative agent baseline without
separate evidence. If the Direct runner identity/source or any paired budget
differs, report the comparison as unmatched rather than causal.

Record at least: task and oracle SHA-256, source revision, workspace seed/hash,
condition name, runner name/version/source hash, exact launch command, model
digest, request payload/settings, tool set, max-context and total-token
budgets, wall-clock limit, stopping rule, raw trace location/hash, final file
hashes, oracle output, and exit status. Repeat each condition and publish all
runs, including failures. This fixture does not reproduce or validate the
historical ARGUS or long-horizon results.
