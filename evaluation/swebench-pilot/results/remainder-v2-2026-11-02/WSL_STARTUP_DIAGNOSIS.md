# WSL startup diagnosis (read-only, Windows side)

Date: 2026-11-02. Ubuntu-22.04 is `Stopped`. No start retry, no shutdown, no
service restart, no config change, no fsck/mount/delete. Machine-readable:
`WSL_STARTUP_DIAGNOSIS.json`.

## What the evidence says

**Config and runtime**

- `C:\Users\user\.wslconfig` (34 B): `memory=100GB`, `swap=32GB`.
- Host RAM 130,428 MB total, 102,871 MB available (memory setting is not the issue).
- C: free 5.4 GiB.
- Services: `WslService` RUNNING, `vmcompute` RUNNING, `hns` RUNNING,
  `LxssManager` not installed (normal for Store WSL2).
- Processes: `vmmemWSL` pid 2352 started **2026-10-30 19:19:57** and `vmcompute`
  from the same boot - the utility VM was alive until the failure; no guest
  docker/containerd now.
- WSL 2.6.3.0, kernel 6.6.87.2-1.
- `ext4.vhdx`: 184,516,870,144 B, attributes `Archive` (not read-only), mtime
  `23:26:56` (the failed start touched it). Not sparse.

**Events at the failure**

- Hyper-V-Worker-Admin 18508 at `23:26:56.646` (Information): VM
  `86042566-…` **"was shut down by the guest operating system"**; VmSwitch port
  deletions the same second.
- Hyper-V-Compute-Operational: only Information events (2008/2009,
  `result 0x0`) at `23:26:57`.
- Hyper-V-Compute-Admin: only "Host Compute Service started" from Oct 21/23/30.
- No WSL, Hyper-V, storage or disk error events; Application log has no errors in
  the last 2 h. The only nearby System errors are unrelated Volsnap warnings at
  14:31 (shadow-copy growth while C: was full) and DCOM permission warnings.
- `wsl --system` also fails with the same `E_FAIL` now that the VM is down.

**Swap file**

- No `swap.vhdx` at `%TEMP%`, `%LOCALAPPDATA%\Temp` or the user `Temp` path.
- With `swap=32GB` configured and 5.4 GiB free, swap VHDX creation/growth at VM
  start is a plausible blocker. A dynamic VHDX may not preallocate the full
  32 GB, so this is a **candidate, not proven**.

## What is NOT proven

`Wsl/Service/CreateInstance/E_FAIL` (failure step 2) does **not** prove ext4
corruption. No storage/VHD error event exists at the failure time; the VM was
torn down by its own guest and the error surfaced as instance-creation failure.
The earlier line "host space is no longer the immediate blocker" is corrected:
swap/VM startup remains a live candidate, and disk-full can still break swap.

The two candidate layers are:

1. utility-VM startup/config (swap sizing vs 5.4 GiB free, VM guest init
   shutting the VM down);
2. distro instance creation (HCS) failing, with WSL then stopping the VM.

An ext4/VHD problem remains possible but is **not evidenced by these events**.

## Actionable bounded options (none executed)

| rank | option | risk | needs |
| --- | --- | --- | --- |
| 1 | back up `.wslconfig`, set `swap=0` (or remove the line), then **one** start attempt | low, reversible | explicit settings-change permission |
| 2 | if still `E_FAIL`: controlled `wsl --shutdown` (and/or WslService restart), then one start | medium, kills the utility VM | explicit permission |
| 3 | if the VM starts but the distro still fails: attach the vhdx read-only (`wsl --mount --vhd … --bare`) and inspect before any repair | medium | explicit permission |
| 4 | in-place fsck of the existing vhdx | high, data-loss risk | explicit risk approval |
| 5 | image the vhdx first | low, but needs ~185 GB free (only 5.4 GiB) | external storage; **not a prerequisite** |

Option 1 is the cheapest test that separates a swap/VM-config problem from a
filesystem problem, and it is reversible. If it still fails, option 2 tests the
utility-VM layer before touching the filesystem. Only after those should the
filesystem itself be suspected.

## Not done

No guest access (still `Stopped`), so the `/home/iclab` redundant-file inventory
and the `runs-export-v2` cell counts remain unreadable/unknown. No inference,
regrade, cleanup, deletion or source change.
