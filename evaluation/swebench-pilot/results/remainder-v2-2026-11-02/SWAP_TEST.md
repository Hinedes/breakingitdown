# swap=0 test: Ubuntu-22.04 starts, filesystem healthy

Date: 2026-11-02. User-approved reversible setting test + one start. No repair,
no shutdown, no service restart, no deletion. Machine-readable: `SWAP_TEST.json`.

## Config change (temporary, reversible)

| item | value |
| --- | --- |
| file | `C:\Users\user\.wslconfig` |
| before | 34 B, sha256 `E64FFED8…C487` |
| after | 31 B, sha256 `E044C915…FD77` |
| backup | `C:\Users\user\.wslconfig.bak-20261102-233646` (34 B, same sha) |
| change | only `swap=32GB` -> `swap=0`; BOM, `memory=100GB`, line endings untouched |

Restore command (do **not** run while C: is full):

```
Copy-Item -LiteralPath 'C:\Users\user\.wslconfig.bak-20261102-233646' -Destination 'C:\Users\user\.wslconfig' -Force
```

## Result: the distro starts

```
$ wsl -d Ubuntu-22.04 -u iclab -- /bin/df -hT / /mnt/c
Filesystem     Type  Size  Used Avail Use% Mounted on
/dev/sdc       ext4  251G  170G   69G  72% /
C:\            9p    931G  926G  5.4G 100% /mnt/c
```

- Kernel `6.6.87.2-microsoft-standard-WSL2`, root `/dev/sdc` mounted **rw**
  (`discard, errors=remount-ro`).
- Journal: **`EXT4-fs (sdc): recovery complete`** then `mounted ... r/w`; no EXT4
  errors in dmesg this boot (only dxg/p9 warnings).
- Swap 0 B; memory 98 GiB (the `memory=100GB` line still applies).
- Host C: free `5,834,211,328` B before -> `5,726,736,384` B after (small delta).

Conclusion: the 32 GB swap allocation was the startup blocker, not ext4 damage.
The earlier `E_FAIL` was a VM/startup failure and is now explained.

## Evidence counts (from `complete.json`, not directory counts)

| measure | value |
| --- | --- |
| cell dirs in `runs-export-v2` | 24 |
| checkpoints | 23 |
| prefix v2 (4) + new (19) | 4 + 19 |
| completed=true | 23 |
| official resolved / unresolved | 3 / 20 |
| error>0 / infra>0 | 0 / 0 |
| new statuses | 11 ok, 8 agent-error (all unresolved) |
| incomplete without checkpoint | `django__django-15973--worker` |

The 3 resolved are the metric-blind `django__django-10097` prefix cells; the 19
new cells are genuinely unresolved (not grader errors). The 4 prefix records are
intact (`artifact_scoring=v2-prefix-reuse`, inference `f499…`, export `c637…`).
No inference, regrade or retry was run.

## State to keep in mind

- `swap=0` stays in place **temporarily**; backup retained. Restoring 32 GB while
  C: has ~5.3 GB free will likely reintroduce the startup failure.
- Guest side has 69 GiB free, host C: 5.3 GiB; freeing guest files will not return
  host bytes until a separate `fstrim`/compact is approved and safe.
- The interrupted 56-cell run stopped after 19 new completed cells plus one
  incomplete cell; the remaining cells were never started. Any continuation
  needs a primary decision (no resume was performed).

## Not done

No `wsl --shutdown`, service restart, host reboot, fsck, mount/remount, resize,
compact, prune or delete; no model/docker/agent/grade start.
