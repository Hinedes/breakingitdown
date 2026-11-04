# Docker accounting and image cleanup

Date: 2026-11-03. The trusted rootless daemon was started from the existing
script for accounting, 8 verified-unused images were removed, and the daemon was
stopped. No prune, no containers/volumes, no model/cell/grade. Machine-readable:
`DOCKER_ACCOUNTING.json`, `DOCKER_IMAGE_CLEANUP.json`.

## Docker report (before removal)

| item | value |
| --- | --- |
| images | 23 unique (20 task + sanity + alpine + bid-buildtest) |
| images size | 39.86 GB logical (shared layers counted once) |
| reclaimable | 30.23 GB (75%) |
| containers | 0 |
| volumes | 0 |
| build cache | 154 records, 39.86 GB shared, **622.6 kB private / reclaimable** |
| dangling images | 0 |

The build cache is not the bloat: its 39.86 GB is the same shared image-layer
data, and `builder prune` alone would free ~0.6 MB while images exist.

## Physical vs logical

On-disk store before cleanup: `66,976,154,292` B (after the daemon's startup GC
of ~2.48 GB from the earlier 69.46 GB). Docker reports 39.86 GB of image content;
the rest is unpacked overlayfs snapshots (55 GB class) plus metadata and the
content store.

**Removed 8 images** (7 fully-graded issues + sanity, both sanity tags):

| tag | logical size |
| --- | --- |
| django-10097 | 4.51 GB |
| django-11133 | 4.27 GB |
| django-11820 | 4.27 GB |
| django-12325 | 4.28 GB |
| django-13212 | 4.29 GB |
| django-15499 | 4.49 GB |
| django-15851 | 4.49 GB |
| sympy-20590 (sanity, 2 tags) | 4.07 GB |

**Logical sum 34.66 GB → actual physical freed 5.46 GB** (du `66,976,154,292` →
`61,511,925,403`). The difference is shared base layers that remain for the 13
retained task images. Docker never frees 7 × 4.5 GB here — exactly the shared-layer
illusion.

## Retained

13 task images still needed for grading/remaining arms: `15973` (worker
unfinished), `16662`, `matplotlib-23314`, `psf-requests-1142`,
`pydata-xarray-6938`, `pylint-4661`, `pylint-7277`, `pytest-10356`,
`sphinx-7454`, `sphinx-7757`, `sympy-12489`, `sympy-20916`, `sympy-24213`.
Also untouched: `alpine:3.20`, `bid-buildtest:latest` (not pilot scope).

All removed IDs/tags and retained IDs are in `DOCKER_IMAGE_CLEANUP.json` for
rebuild reference; images are recreatable from the pinned task definitions
(hours of rebuild).

## Space

| measure | before | after |
| --- | --- | --- |
| docker store du | 66,976,154,292 | 61,511,925,403 |
| guest used | 169,661,747,200 | 163,914,027,008 |
| guest available | 86,005,063,680 | 91,752,779,776 (85.4 GiB) |
| Windows C: free | 5,705,830,400 | 5,567,516,672 (~5.2 GB) |

Host C: is unchanged (guest space only). **A separately approved fstrim +
compaction is still required** to return these guest bytes to Windows.

## Max potential if all pilot images were removed

Docker's own reclaimable number before removal was **30.23 GB** (of 39.86 GB
logical). Removing every task image would free at most ~30 GB guest-side, would
block all remaining grading (13 needed images), and would cost hours to rebuild.
Not recommended.

## VHD compaction preflight (not executed)

1. Ensure no containers/daemons: confirmed none now.
2. Guest: `sudo fstrim -av` (root) to trim free blocks.
3. Stop the distro: `wsl --terminate Ubuntu-22.04` (primary approval; the swap=0
   config stays).
4. Compact the VHD in place: `wsl --manage Ubuntu-22.04 --set-sparse true`
   (WSL 2.6) or `Optimize-VHD`/diskpart with the distro stopped.
5. Host C: needs a few hundred MB of headroom during compaction; currently ~5.2 GB.
6. The vhdx is currently NOT sparse; a 185 GB image is **not** required for
   in-place compaction, but the operation is a write on the VHD and needs primary
   review with data-loss awareness.

## Not done

No `docker system prune`, no container/volume removal, no build-cache prune, no
trim/compact, no WSL stop, no model/cell/grade/source/git change.
