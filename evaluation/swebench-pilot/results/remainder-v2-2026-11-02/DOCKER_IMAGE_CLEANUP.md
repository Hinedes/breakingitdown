# Docker image cleanup ledger

Date: 2026-11-03. `docker image rm` (no `--force`), all rc=0, no conflicts.
Daemon started from the existing rootless script and stopped afterwards.

## Removed (8 images)

| tag | image id | logical |
| --- | --- | --- |
| swebench/…django-10097:latest | `f65dd477…` | 4.51 GB |
| swebench/…django-11133:latest | `779b228b…` | 4.27 GB |
| swebench/…django-11820:latest | `510db3a5…` | 4.27 GB |
| swebench/…django-12325:latest | `9b7e5414…` | 4.28 GB |
| swebench/…django-13212:latest | `a200f61d…` | 4.29 GB |
| swebench/…django-15499:latest | `5136f88d…` | 4.49 GB |
| swebench/…django-15851:latest | `c4be812f…` | 4.49 GB |
| swebench/…sympy-20590:latest + alias `sweb.eval…sympy__sympy-20590` | `4c419785…` | 4.07 GB |

Full 64-char IDs are in `DOCKER_IMAGE_CLEANUP.json`.

**Logical sum 34.66 GB; physically freed 5.46 GB** because the shared base layers
remain in use by the 13 retained task images.

## Retained (13 task images + 2 unrelated)

`15973` (worker unfinished), `16662`, `matplotlib-23314`, `psf-requests-1142`,
`pydata-xarray-6938`, `pylint-4661`, `pylint-7277`, `pytest-10356`,
`sphinx-7454`, `sphinx-7757`, `sympy-12489`, `sympy-20916`, `sympy-24213`;
plus `alpine:3.20` and `bid-buildtest:latest` (not pilot scope, untouched).

## Verification

- `docker image ls` after removal shows exactly the 13 task images above (all
  preserved) + the 2 unrelated.
- Guest used 169,661,747,200 -> 163,914,027,008; available 91,752,779,776.
- Docker store 66,976,154,292 -> 61,511,925,403 (du).
- Host C: ~5.2 GB free (guest-only reclaim; compaction pending).

## Rebuild

Removed images are recreatable from the frozen task definitions / pinned
Dockerfiles. Rebuild cost is hours and requires the network/apt cache again; the
removal ledger (IDs + tags) is kept to reproduce the exact tags.
