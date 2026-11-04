# Redundant files inventory under /home/iclab (read-only)

Date: 2026-11-02, after the successful `swap=0` start. Nothing deleted.
Machine-readable: `REDUNDANT_FILES.json`.

Guest: Ubuntu-22.04, kernel `6.6.87.2-microsoft-standard-WSL2`, root ext4
251 G with **69 G free** (host C: still ~5.3 G free). Deleting guest files frees
guest space only; host bytes return only after a separately approved
`fstrim`/compact.

## Models

| path | size | note |
| --- | --- | --- |
| `bid-models/Qwen_Qwen3-4B-Instruct-2507-Q8_0.gguf` | 4.28 GB | **needed** by the pilot (expected sha256 `260b5b5b…`); keep |
| `bid-models/Llama-3.2-1B-Instruct-f16.gguf` | 2.48 GB | other study model, not used by this pilot |
| `bid-models/Llama-3.2-3B-Instruct-Q8_0.gguf` | 3.42 GB | other study model, not used by this pilot |

**No duplicate GGUF exists** (maxdepth-5 scan); the Qwen file is singular, so no
hash comparison was needed. No model/checkpoint was deleted.

## Checkpoints

`/home/iclab/grafting-resume/gate-repro/evidence/*.pt` — ~23 files, ~22.6 MB
each, inside a 13.6 GB research directory. These are **unique research evidence**
artifacts, not redundant copies; preserve until the user reviews.

## Cleanup candidates (none executed)

| rank | path | size | kind / risk |
| --- | --- | --- | --- |
| 1 | `/var/cache/apt/archives` | 6.09 GB | apt package cache, recreatable; low risk, sudo |
| 2 | `/home/iclab/.cache/huggingface` | 5.13 GB | HF download cache, re-downloadable; low-medium |
| 3 | `Anaconda3-2023.09-0-*.sh` + `Miniconda3-latest-*.sh` | 1.32 GB | installer archives, re-downloadable; low |
| 4 | `.cache/pip` + `.local/share/uv` + `.npm` | 0.86 GB | package caches; low |
| 5 | `bid-llama-runtime` build objects | 0.89 GB | rebuildable, binary needed; medium |
| 6 | `.local/share/docker` | 66.98 GB | docker data with the 21 SWE-bench images the pilot needs; **high** if pruned blindly |

Ranks 1-5 total **~13.9 GB** of guest space; none touches study data, models or
checkpoints. Rank 6 is not "redundant" without image-level review.

## Protected (never delete)

`/home/iclab/BreakItDown`, `/home/iclab/bid-eval`, `/home/iclab/bid-studies`,
`/home/iclab/bid-benchmarks` (runs, logs, frozen rows), the grafting-resume
checkpoints/evidence, and the Qwen3-4B model file.

## Other large, non-redundant dirs (context)

`.local/share/docker` 67 GB, `.local/lib` 8.46 GB, `.venvs` 7.4 GB,
`.rustup` 1.38 GB, `ARGUS` 7.7 GB, `zeroball` 5.1 GB, `arw` 3.17 GB,
`opt` 2.26 GB, `Proteus` 1.45 GB — user/study or environment data; preserved.
