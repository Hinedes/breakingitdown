# Docker disk audit (read-only)

Date: 2026-11-03. The Docker daemon is **off** (stopped with the VM restart), so
no `docker system df` / `images` data exists. Nothing was started or deleted.
All numbers below are from the on-disk store. Machine-readable:
`DOCKER_SPACE_AUDIT.json`.

## Direct answer: where the 64.7 GiB is

| component | GiB | what it is |
| --- | --- | --- |
| containerd overlayfs snapshots | **55.07** | unpacked image layers (284 snapshot dirs) |
| containerd content store | **9.61** | compressed layer blobs (same layers, source form) |
| buildkit | 0.001 | build cache — effectively empty |
| containers / logs | 0.00 | 0 containers, 0 logs |
| volumes | 0.00003 | 1 tiny volume |
| metadata/misc | 0.01 | image metadata, network, plugins |

Total `69,458,313,216` B = **64.69 GiB**. This store uses the **containerd
snapshotter**, not classic `overlay2`.

284 snapshot directories correspond to the shared+unique layers of the expected
**21 images** (20 frozen issues + the sympy-20590 sanity image). The snapshotter
stores each layer digest once, so 64.7 GiB is much less than the sum of the 21
logical image sizes — that is why "21 images" does not mean 21 × several GiB of
unique data.

## Why one image per SWE-bench issue

Each task pins a Dockerfile with historical apt/conda/pip dependencies and the
repository at a specific base commit. Official grading must run inside that exact
environment, so a single blank Python container cannot reproduce the dependency
graph; a wrong env would silently change F2P/P2P outcomes. The layers shared
across all Django images (Ubuntu jammy, conda, common wheels) are stored once by
the snapshotter, so only the per-issue deltas are duplicated.

## Build cache, containers, logs

- buildkit: `1,110,016` B (~1 MB) — no meaningful build-cache bloat.
- containers: 0 dirs, no `*-json.log` files.
- volumes: 1 (28,672 B).
- No dangling image can be confirmed without the daemon; the on-disk metadata is
  not a safe substitute for `docker system df -v`.

## Reclaimable (honest limits)

| item | reclaimable |
| --- | --- |
| build cache | ~1 MB |
| container logs | 0 |
| stopped containers | 0 |
| dangling/unreferenced images | **unknown** (daemon off) |
| per-issue images | not quantifiable; shared layers dominate |

Which images could be retired: 7 issues have all three arms checkpointed —
`django__django-10097`, `11133`, `11820`, `12325`, `13212`, `15499`, `15851`.
`15973` has bid+mini only (worker incomplete), so its image is still needed.
Deleting a completed issue's image would free only that issue's unique layers, and
the images are still useful for grade reproducibility; rebuild cost is hours.
**Not recommended now, and not quantified.**

## Recommendation

1. Do nothing now: build cache is ~1 MB, no containers or logs exist.
2. Do not prune with the daemon off — tags/layers cannot be verified.
3. If host space becomes critical, with explicit approval: start the trusted
   rootless daemon, run `docker system df -v` and `docker images`, then consider
   removing only the per-issue images of fully-complete issues — never `-a`
   prune, never shared base layers, never the frozen rows/definitions.
4. Docker data lives inside the WSL VHD; even a large prune frees guest space
   only. Returning bytes to C: still needs a separately approved
   `fstrim`/compact.

## Not done

No daemon start/restart, no container/image/volume/build-cache removal, no model,
cell, grade, source or git change.
