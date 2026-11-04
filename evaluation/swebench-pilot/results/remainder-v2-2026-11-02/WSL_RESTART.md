# Controlled Ubuntu-22.04 restart - result

Date: 2026-11-02. Authorized scope: restart **only** Ubuntu-22.04; no host reboot,
no global `wsl --shutdown`, no repair tools, no deletions, no models/cells.

Machine-readable copy: `WSL_RESTART.json`.

## Preconditions

- C: free `5,797,543,936` B (~5.4 GiB) >= 3 GiB guard.
- WSL: `Ubuntu-22.04  Running  2`, and it is the only distro.

## Steps and actual results

| step | command | result |
| --- | --- | --- |
| terminate | `wsl --terminate Ubuntu-22.04` | **success** ("The operation completed successfully."), state `Stopped` |
| start (once) | `wsl -d Ubuntu-22.04 -u iclab -- /bin/df -hT / /mnt/c` | **failed**: "The distribution failed to start. Error code: 6, failure step: 2" / `Wsl/Service/CreateInstance/E_FAIL` |
| kernel tail | `wsl --system -- /bin/dmesg --level=err,warn --notime` | same `E_FAIL` - the system distro/utility VM will not start either |

No retry was made after the failed start. No host reset, fsck, remount, compact,
resize, prune or daemon action was attempted.

## Current state

- Distro: `Stopped`.
- C: free `5,796,823,040` B (~5.4 GiB), total 930.5 GB; still only ~1 MB
  unallocated on the single disk, so no expansion.
- Guest processes: none (the VM is down; the rootless `dockerd`/`containerd`
  observed earlier are gone with it). No LLM, proxy or pilot process anywhere.
- The restart did not bring the filesystem up: mount read/write is **not** proven
  healthy, and the failure at instance-creation step 2 means the journal replay
  (if attempted by WSL) did not result in a startable distro.

## What we still cannot read

- `runs-export-v2` cell count / `complete.json` set, `logs/evaluation` grades,
  `runtime/remainder-v2-2026-11-02/final.json`.
- Therefore: new cells completed/graded = **unknown**; the only confirmed
  preserved results are the 4 prefix derived cells. No inference or regrade was
  run.

## Interpretation

Corrected after the Windows-side event review (`WSL_STARTUP_DIAGNOSIS.md`): the
`E_FAIL` does **not** prove the ext4 filesystem is broken. No storage/VHD error
event exists at the failure time; the utility VM was shut down by its guest
operating system (Hyper-V-Worker 18508). Host space is **not** ruled out as a
factor - `.wslconfig` requests `swap=32GB` with only ~5.4 GiB free - so swap/VM
startup remains a live candidate alongside a distro-instance/HCS failure.

## Options for the primary (not executed)

1. Image `ext4.vhdx` (or copy it) while the distro is stopped, then run
   `e2fsck -n` on the copy to size the damage; if repairable, `e2fsck -y` on the
   copy and mount read-only to extract `runs-export-v2`, `logs/evaluation`,
   `runtime`, `frozen`.
2. If the copy is healthy, decide whether to repair the original or rebuild the
   distro and re-attach the recovered data.
3. Do not fsck the live vhdx. Do not restart repeatedly - each attempt is a write
   on a damaged journal.

Redundant-file inventory under `/home/iclab` was **not** possible because the
guest never came up; no `REDUNDANT_FILES.md` was produced. No guest cleanup was
attempted.
