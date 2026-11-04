# TEMP cleanup and guest-access probe (user-authorized, scoped)

Date: 2026-11-02. Phase 1 deleted only stale regular files under the two exact
TEMP roots. Phase 2 was one bounded read-only guest access attempt. No model,
cell, grade, source edit or git operation.

Machine-readable copy: `TEMP_CLEANUP.json`.

## Result

| measure | value |
| --- | --- |
| C: free before | `30,511,104` B (30.5 MB) |
| C: free after | `5,797,646,336` B (5.80 GB) |
| host space recovered | **`5,767,135,232` B (~5.4 GiB)** |
| files deleted | 1,042 (959 user Temp + 83 Windows Temp) |
| bytes deleted | 5,765,956,218 B |
| empty dirs removed | 557 (550 + 7), only inside the two roots |

### Per root

| root | deleted | bytes | removed dirs | skipped |
| --- | --- | --- | --- | --- |
| `C:\Users\user\AppData\Local\Temp` | 959 | 5,485,643,549 | 550 | 64 recent, 2,782 protected |
| `C:\Windows\Temp` | 83 | 280,312,669 | 7 | 12 recent, 1 locked |

Both roots were verified canonical (case-insensitive), existing, and **not** a
reparse point. Deletion used `[IO.File]::Delete` on regular files only; directory
removal used `[IO.Directory]::Delete(path,false)` (empty only), bottom-up.

### What was skipped by policy

- files modified within 24 h (cutoff `2026-11-01T23:21:45`);
- reparse points/symlinks (never followed);
- model/checkpoint/archive/dataset extensions (`.gguf`, `.safetensors`, `.ckpt`,
  `.pt`, `.pth`, `.onnx`, `.bin`, `.parquet`, `.zip`, `.iso`, `.vhdx`, ...);
- any path whose name contains project/source/study/artifact/breakitdown/bid-/
  checkpoint/dataset/model/repo/experiment/backup/archive;
- locked/access-denied files (1 in Windows Temp).

Top deleted items were Visual Studio/VS Code installers, Copilot/Android SDK
`.msi`/`.vsix` payloads and RDP auto-trace `.etl` logs (paths and sizes in the
JSON; no file contents read or exported).

Not touched: Recycle Bin (not approved), Downloads, any `.vhdx`, models,
checkpoints, archives, project/study data, or anything outside the two roots.

## Guest access probe

One command, bounded: `wsl -d Ubuntu-22.04 -u iclab -- /bin/df -h /` ->
`getpwnam(iclab) failed 5` / `WSL_E_USER_NOT_FOUND`. The device is still offline;
**host space alone did not revive it**. No restart, shutdown, remount, fsck,
compact, daemon stop or repair was attempted.

## Space picture now

- C: has ~5.8 GB free (was 30 MB); the disk still has only 1 MB unallocated, so
  C: still cannot be expanded.
- The WSL vhdx is still ~171.8 GiB and fully allocated; the guest's ~72 GiB free
  remains virtual and will not return host bytes without trim/compact, which
  requires a healthy guest.
- A controlled WSL restart / journal replay is now possible space-wise but is a
  **primary decision** with explicit data-loss risk. Export small evidence first
  once the guest is readable.

## Follow-up candidates (not deleted, not authorized)

Inside the guest (unreadable right now, so not audited): duplicate GGUF copies,
checkpoint copies, `~/.cache/huggingface`, pip/uv/npm caches, source build trees,
downloaded archives. That audit requires the guest to come back and explicit
approval; it was not attempted here.
