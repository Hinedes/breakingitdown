# RUN sandbox assessment

**Historical decision at epoch 13: keep RUN denied.** The existing executor was an oracle sandbox, not
a persistent Worker executor. It copies the workspace into a fresh chroot,
runs with no network, then discards the copy. There is no safe, tested
copy-back path for Worker edits, so an execution-enabled agent condition was
not run.

## Bounded tests on Ubuntu 22.04

Using the epoch-13 sandbox source and its configured Python runtime:

- Host model/home paths and `/proc`, `/sys`, and `/dev/null` were absent inside
  the chroot; host loopback port 8081 and external `1.1.1.1:53` were unreachable.
- A symlink in the copied workspace pointing to `/home/iclab/bid-models` did
  not expose its host target.
- Sandbox writes to `docs/task.md`, `docs/todo.md`, `docs/manager.md`,
  `docs/worker.md`, and `.bid/control` left every corresponding host file
  unchanged. `.bid` itself is excluded from the copied tree.
- A write under `/work` was visible during the sandbox call but absent from
  the source workspace afterward, confirming the missing persistence behavior.
- A timed-out sandbox process and a spawned descendant were both gone after
  termination; a 0.4-second timeout completed cleanup in 2.402 seconds.

These are bounded behavior checks, not a security audit of Linux namespaces,
kernel escape resistance, or hostile filesystem races. In addition, control
documents such as `docs/todo.md` are present in the disposable copy and can be
read or changed there. Tests show the original files are not modified, but do
not prove a future merge-back policy safe. Do not add persistence by copying
the whole sandbox tree back: any future executor would need a narrowly scoped,
validated artifact transfer design and dedicated hostile-input tests first.

No sandbox or Worker execution code was changed, and RUN remains unavailable.

## Primary implementation after this assessment

The missing persistence path is implemented as a separate Worker executor, without copying an untrusted tree back. A private rootless mount/PID/network namespace bind-mounts only the disposable project writable, hides existing BID control roots behind read-only empty mounts, mounts Python and system runtime read-only, removes capabilities, and exposes neither the host home nor credentials. The normal harness still checks/restores protected control state around RUN.

`run_executor` is a trusted embedding callback, not an environment switch granting host execution. Default RUN remains denied. Only the explicitly isolated study condition supplies this callback; its generic READ/WRITE actions and contained RUN capability are identical for custom Direct and BID.

Epochs 14–18 passed bounded checks for persistent ordinary edits, unchanged controls, inaccessible host home/network, read-only runtime, symlink isolation, and timeout cleanup. The implementation fails closed outside its tested x86-64/Linux 5.12+ environment. It is not a kernel security audit or an aggregate disk/memory cgroup guarantee; experiments use disposable synthetic workspaces and finite command/file/process/time limits.

A follow-up hardening keeps namespace PID 1 as a trusted, non-dumpable supervisor rather than executing model code as PID 1. This prevents a command creating its own session from evading the parent's process-group timeout. The regression launches a session-detaching process and checks that a delayed descendant cannot write after timeout. This change is not retroactively attributed to the immutable epoch-18 cohort.
