# Epoch 19: namespace supervisor hardening

No epoch-18 result is rewritten. That task cohort runs from its frozen source and uses closed synthetic tasks. This follow-up keeps the namespace init process trusted, non-dumpable, and in the parent's process group while model commands execute as children. Session-detaching commands and their descendants must not outlive a timeout. Private IPC/UTS namespaces and the existing mount/network/PID isolation are explicit.

Validate ordinary persistent edits, protected and read-only paths, symlinks, remount attempts from a nested user namespace, and a session-detaching descendant that schedules a delayed write. Record the exact suite result before any new epoch-19 model calls. There is no claim of aggregate cgroup resource enforcement or a kernel escape audit.

This is runtime hardening, not a change to the frozen task/oracle definitions, role model, semantic scoring, seeds, or the constrained-decoding policy. Any subsequent model results use epoch 19 identity and remain separate from epoch 18. The 08:00 Taipei cutoff remains unchanged.
