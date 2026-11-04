# I/O failure during the 56-cell continuation - confirmed diagnosis

Date: 2026-11-02. Read-only. `result.json` (0 bytes) left untouched; this file is
the incident record. No repair, restart, delete, prune or service change was
performed.

## Root cause (confirmed)

The **Windows host volume holding the WSL virtual disk is full**, so the dynamic
ext4 VHD could not grow and the kernel took the device offline:

- `C:` total `999,127,248,896` bytes, free `32,182,272` bytes (~30.7 MiB and
  shrinking; it was ~34 MiB minutes earlier). Full audit: `SPACE_AUDIT.md`.
- **No expansion is possible**: single disk, only 1 MB unallocated, no second
  volume. A dynamic VHD also never adds host capacity.
- `ext4.vhdx` = `184,516,870,144` bytes (~171.8 GiB), last write `2026-11-02 22:06`
  local - the exact failure window.
- Kernel (saved at `wsl-kernel-io-error.txt`): `hv_storvsc` status failure and
  "rejecting I/O to offline device sdd", repeated `Buffer I/O error on dev sdd`,
  `EXT4-fs ... I/O error writing inode`, `EXT4-fs: journal aborted`, `JBD2:
  journal superblock write I/O error`, `EXT4-fs: I/O error writing superblock`.
- Guest `df` earlier reported ~72 GiB free **inside ext4**; that is not host free
  space and must not be quoted as disk health.

## Current state evidence

| probe | result |
| --- | --- |
| `wsl -l -v` | `Ubuntu-22.04  Running  2` |
| guest command as `iclab` / `root` | `getpwnam(<user>) failed 5`, `WSL_E_USER_NOT_FOUND` (passwd read EIO) |
| `nvida-smi` (primary) | 391 MiB used, baseline; no resident LLM |
| `wsl --system -- ps -ef` (primary) | no LLM / proxy / pilot / evaluator; rootless `dockerd` + `containerd` still live (PIDs 3088/3103/3120/3129/3156, containerd start 13:51Z) |
| system-namespace path probe | `ls -la /proc/3129/root/.../runs-export-v2` partially succeeded but reported `Input/output error`; a follow-up `ls | wc -l` exceeded 120 s and was abandoned |

The daemons are **not** to be stopped without a primary decision; they are the
only live owner of the guest namespace.

## Completed / in-flight / pending

**Unknown.** No cell is claimed finished or unfinished.

Observed from the partial listing (unreliable, EIO interrupted it): cell
directories exist for 10097, 11133, 11820, 12325, 13212, 15499, 15851 - 21 cell
dirs visible (7 issues x 3), timestamps `21:32-22:04` local (`13:32-14:04Z`).
Whether each has a `complete.json` cannot be read. The job's own final read of
`runtime/remainder-v2-2026-11-02/pilot.log` failed with `Errno 5`, and
`final.json` is missing/unreadable.

The 4 prefix cells are the only confirmed completed results
(`results/export-correction-2026-11-02/prefix-result.json`, locally preserved).

## Preserved

- Source `c63701b9c9c623053ed387f816738b411071ef74a6a7685f1cb3ab39058817da`,
  manifest `b0cf7f6d2b3dfddbd9e769762d00d6569b88b31a9d0a420b87685d424d33b03d`.
- The 4 derived checkpoints, all original f499 records, logs, frozen rows, models
  and the 171.8 GiB vhdx (untouched; it is the evidence).
- Nothing was deleted, moved, repaired or restarted.

## Windows-side space candidates (read-only audit, nothing deleted)

| path | size | ownership |
| --- | --- | --- |
| `C:\Users\user\AppData\Local\Packages\CanonicalGroupLimited.Ubuntu22.04LTS_79rhkp1fndgsc\LocalState\ext4.vhdx` | 184,516,870,144 B | task WSL disk (the failed device); shrinking needs WSL |
| `C:\Users\user\Downloads` | 8,354,002 B total | not ours (AnyDesk + desktop.ini) |
| `C:\Users\user\AppData\Local\Temp\opencode` | does not exist on this host | - |

No model/GGUF copy exists on the Windows side (the 4.3 GiB GGUF lives inside the
WSL disk). No task-owned Windows download/build cache was found. Freeing `C:`
therefore requires either the owner freeing unrelated data or moving/recreating
the vhdx on another volume - both need user authorization.

## Recovery options, ranked by data safety

1. **Freeze and keep the vhdx untouched** (current state). Evidence is already
   captured; no further guest reads.
2. **Free host space or move the vhdx** (user decision). Moving requires WSL
   stopped; a controlled `wsl --shutdown` can trigger journal replay, so capture
   the current vhdx first.
3. **Image the vhdx** before any repair; hash it, keep the original, analyse the
   copy.
4. **`e2fsck -n` on the copy** (read-only) to size the damage; if repairable,
   `e2fsck -y` on the copy, mount read-only and extract
   `runs-export-v2`, `logs/evaluation`, `runtime`, `frozen`.
5. **Repair the original only after** a verified image and an extracted copy
   exist. Never run fsck against the live vhdx.

## Accounting once readable

Count `runs-export-v2/*/complete.json` (60 slots: 4 derived + created new cells),
distinguish partial cell dirs from graded checkpoints, and read
`logs/evaluation/<run_id>` for each graded cell. Until then the counts stay
`unknown`.
