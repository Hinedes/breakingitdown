# Remainder v2 (56 cells) - launch record

Launched by the primary on 2026-11-02 in a main-owned background shell
(job `sh_19c7cc0040012cTvW2bjeQMx6G`). No model reruns for the 4 prefix cells.

## Inputs

- source: `bid-source-c63701b9c9c6` (`c63701b9c9c623053ed387f816738b411071ef74a6a7685f1cb3ab39058817da`)
- manifest: `manifest-c63701b9c9c6.json` (`b0cf7f6d2b3dfddbd9e769762d00d6569b88b31a9d0a420b87685d424d33b03d`)
- run root: `runs-export-v2` (contains the 4 derived prefix checkpoints)
- runtime: `runtime/remainder-v2-2026-11-02`
- preflight: 4 derived present / 56 missing, live state clean, 72.17 GiB free

## Prefix record (already graded)

| key | new grade run id | result |
| --- | --- | --- |
| django__django-10097--worker | `…-0c12f35a68ae` | completed 1, resolved 1 (metric-blind, empty patch) |
| django__django-10097--mini | `…-511cea73c8c2` | completed 1, resolved 1 (metric-blind, empty patch) |
| django__django-10097--bid | `…-616e5f5a8eca` | completed 1, resolved 1 (metric-blind, empty patch) |
| django__django-11133--mini | `…-685ee1a253e5` | completed 1, unresolved 1, error 0, infra 0 (old error 1 retained) |

Derived controller `status.json` still holds the original model patch hash; the
corrected score comes from the recovered patch, not from the original status.

## Status

Running. **No outcome is claimed here**; the 56 results are reported only after
the primary's completion notification. Export target:
`evaluation/swebench-pilot/results/remainder-v2-2026-11-02/`.

## Exporter

`C:\Users\hind\AppData\Local\Temp\opencode\bid_swe_export.py`
(sha256 `f3fa4542e3425ffd2fa69b0062c7ab1f58e18073d8de35f92c2572e6fa182408`),
run with explicit inputs after completion:

```
$B/venv/bin/python bid_swe_export.py \
  --runs-root $B/runs-export-v2 \
  --manifest $B/manifest-c63701b9c9c6.json \
  --source-root $B/bid-source-c63701b9c9c6 \
  --out <controller results dir>
```
