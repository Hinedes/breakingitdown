# C: space audit and expansion feasibility (read-only)

Date: 2026-11-02. No delete, repair, resize, restart or settings change was made.
Host `DESKTOP-Q12S8Q1`. Machine-readable copy: `SPACE_AUDIT.json`.

## Short answer

- **C: cannot be expanded.** It is the only volume on the only disk, and the disk
  has **1 MB unallocated**. A dynamic VHD never adds host capacity either.
- **C: is full**: `999,127,248,896` B total, **`32,182,272` B free (30.7 MB)**,
  `993,126,891,520` B used. Free space was ~34 MiB minutes earlier and is
  shrinking.
- Easy, low-risk recovery without touching user data is only **~6.5 GiB**
  (temp/trash/update cache), plus up to ~8 GiB if the pagefile is shrunk or moved
  by a system setting (needs reboot). That is a small but real margin for a
  controlled WSL recovery attempt; it is not enough to treat the disk as healthy.

## Geometry (diskpart/fsutil; WMI/Get-Disk returned empty records)

| item | value |
| --- | --- |
| Disk 0 | 931 GB, Online, GPT, free (unallocated) **1024 KB** |
| Volume C: | NTFS, 930 GB, Boot |
| Volume (no letter) | FAT32, 100 MB, System |
| Volume (no letter) | NTFS, 909 MB, Hidden (WinRE) |
| other disks / removable | none (`fsutil fsinfo drives` = C: only) |
| expand supported | **no** - nothing adjacent to grow into |

`Get-Disk`, `Get-Volume`, `Get-CimInstance Win32_LogicalDisk` all returned empty
property sets (WMI/storage service unhealthy on the full host). `fsutil` and
`diskpart` were used instead, both read-only.

## WSL disk

`…\Packages\CanonicalGroupLimited.Ubuntu22.04LTS_79rhkp1fndgsc\LocalState\ext4.vhdx`

- 184,516,870,144 B logical, 184,516,857,856 B valid data, **not sparse** -> it
  really occupies ~171.8 GiB on C:.
- Guest `72 GiB free` is inside ext4. It returns host bytes only after trim +
  compact, which needs a healthy guest and a WSL shutdown; not possible while the
  device is offline.

## Where the 925 GiB goes (measured with `robocopy /L`, bounded)

| path | bytes | GiB | note |
| --- | --- | --- | --- |
| `C:\Users` | 902,598,665,432 | 840.6 | all profiles |
| `C:\Users\jay` | 363,553,108,460 | 338.6 | user data, not ours |
| `C:\Users\user` | 248,886,972,498 | 231.8 | contains WSL |
| `…\user\AppData\Local\Packages` | 185,167,757,594 | 172.5 | includes the 171.8 GiB vhdx |
| `C:\Users\che` | 116,203,118,933 | 108.2 | user data, not ours |
| `C:\Windows` | 47,679,936,727 | 44.4 | |
| `C:\ProgramData` | 12,976,096,553 | 12.1 | |
| `C:\pagefile.sys` | 8,589,934,592 | 8.0 | system setting, not a file delete |
| `C:\Users\user\AppData\Local\Temp` | 6,297,253,895 | 5.9 | temp, recreatable |
| `C:\Users\TEMP` | 1,315,567,906 | 1.2 | |
| `C:\Users\uksha` | 1,112,780,728 | 1.0 | |
| `C:\$Recycle.Bin` | 418,165,234 | 0.39 | user trash |
| `C:\Windows\Temp` | 282,624,129 | 0.26 | recreatable |
| `C:\Users\Public` | 75,801,623 | 0.07 | |
| `C:\Windows\SoftwareDistribution\Download` | 1,284,406 | 0.001 | recreatable |
| `C:\swapfile.sys` | 16,777,216 | 0.016 | |

Not measured (safe to record as unknown, no false completeness): `C:\Program Files`
and `(x86)` (remote-shell parenthesis quoting failed), `C:\Users\WsiAccount`
(robocopy produced no output; access), `C:\Users\Default`, `C:\Recovery`.
Measured profile sum (~731 GB) vs `C:\Users` total (902.6 GB) leaves ~171 GB in
those access-denied/unmeasured dirs - **not** claimed as freeable.

No task-owned Windows download/build cache was found: no GGUF on the Windows side,
no `%LOCALAPPDATA%\Temp\opencode`, no Docker Desktop data. The 171.8 GiB vhdx is
the task's only large Windows footprint.

## Cleanup candidates (nothing deleted)

| rank | candidate | host bytes | risk | needs |
| --- | --- | --- | --- | --- |
| 1 | `%LOCALAPPDATA%\Temp` (user) | 5.9 GiB | low, recreatable | user delete approval |
| 2 | pagefile setting (shrink/move) | up to 8.0 GiB | medium, settings + reboot | user/admin approval, reboot |
| 3 | Recycle Bin | 0.39 GiB | low, but user trash | user confirmation |
| 4 | `C:\Windows\Temp` | 0.26 GiB | low | admin delete approval |
| 5 | WU `Download` cache | ~1.2 MB | low | admin delete approval |
| 6 | WSL vhdx compact | up to ~72 GiB *after* recovery | high now (offline journal) | recovery plan + approval |

Easy total without the vhdx: **~6.5 GiB** (ranks 1+3+4+5); with the pagefile
setting: **~14.5 GiB**. This is the realistic "small host space" plan the user
asked for; it is not a 200 GB backup.

## Expansion feasibility verdict

- Grow C: into unallocated space: **impossible** (1 MB free, single disk).
- Add a disk / move the vhdx: **impossible without user hardware/volume changes**
  (no other locally attached volume).
- Guest-side expansion (max 1 TB in WSL) does not create host bytes and does not
  solve the full host.
- Therefore: free C: bytes first (ranks 1-5), then a controlled recovery attempt
  (restart/journal replay) with explicit data-loss approval; export small evidence
  as soon as the guest is readable; compact the vhdx only after recovery.

## Interplay (explicit)

The guest reports ~72 GiB free inside ext4, but C: has 30 MB free. The VHD is
already fully allocated; writing inside the guest still needs host blocks for
metadata/growth, and the host has none, so the kernel offlined `/dev/sdd`
(`hv_storvsc` failures, `EXT4 journal aborted`). Freeing space inside the guest
would not have helped; freeing host space may.

## Commands used (read-only)

`fsutil volume diskfree C:`, `fsutil fsinfo drives`, `fsutil sparse queryflag`,
`fsutil file queryvaliddata`, `diskpart` `list disk` / `list volume`, `reg query
…\Lxss /s`, `cmd /c dir`, `robocopy <path> C:\__probe__ /L /S /BYTES /XJ /R:0
/W:0`. WMI and `wmic` are unavailable/broken. No writes, no deletions.
