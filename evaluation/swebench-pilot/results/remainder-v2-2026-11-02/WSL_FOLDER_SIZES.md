# WSL folder sizes (read-only, after cache cleanup)

Date: 2026-11-02. `du -x -B1 --max-depth=1`, allocated bytes, symlinks not
followed; GiB = bytes / 2^30. Rows are per-invocation and nested rows are **not**
additive. Other mounts and /proc virtual disks are excluded by `-x`.

Guest now: `used 169,662,173,184` / `avail 86,004,633,600` (80.0 GiB free).
Host C: `5,694,988,288` free (unchanged by guest deletions; compact still needed).

## `/` depth 1

| path | GiB |
| --- | --- |
| `/home` | 133.0 |
| `/usr` | 16.6 |
| `/var` | 0.50 |
| `/opt` | 2.02 |
| `/tmp` | 1.26 |
| `/` total | 153.5 |

`/home` holds almost everything: `/home/iclab` = **133.0 GiB**.

## `/home/iclab` depth 1 (top 27)

| path | GiB | note |
| --- | --- | --- |
| `.local` | 73.7 | mostly `.local/share/docker` |
| `grafting-resume` | 12.8 | research checkpoints/evidence |
| `bid-models` | 9.48 | Qwen3-4B + 2 Llama GGUFs |
| `.venvs` | 6.99 | Python envs |
| `arw` | 5.70 | study data |
| `bid-benchmarks` | 5.66 | pilot root (protected) |
| `zeroball` | 4.79 | study data |
| `ARGUS` | 4.66 | study data |
| `opt` | 2.11 | |
| `.rustup` | 1.47 | toolchain |
| `Proteus` | 1.35 | study data |
| `bid-eval` | 1.02 | protected |
| `bid-llama-runtime` | 0.88 | llama.cpp build |
| `.cache` | 0.39 | after HF hub cleanup |
| `.vscode-server` | 0.38 | |
| `bid-studies` | 0.28 | protected |
| `TALOS` | 0.27 | |
| `.vscode-remote-containers` | 0.23 | |
| `.opencode` | 0.23 | |
| `.triton` | 0.15 | |
| `Grafting-OCFDA` | 0.10 | |
| `.nv` | 0.10 | |
| `.npm` | 0.08 | |
| `.config` | 0.06 | |
| `btop` | 0.02 | |
| `.cargo` | 0.02 | |
| `BreakItDown` | 0.007 | protected |

## Drill-downs

**`.local`** 73.7 GiB: `share` 65.7, `lib` 7.95, `bin` 0.08, `usr` 0.03.

**`.local/share`** 65.7 GiB: **`docker` 64.7 GiB** (21 SWE-bench images + data;
protected, not pruned), `uv` 0.49, `opencode` 0.48.

**`bid-benchmarks/swebench-verified20-2026-11-02`** 5.66 GiB: `runs-export-v2`
3.75 (24 workspace checkouts + checkpoints), `mini-venv` 0.68, `runs` 0.48,
`venv` 0.44, `source` 0.17, `logs` 0.13, others small.

**`grafting-resume`** 12.8 GiB: `.venv312` 7.13, `gate-repro` 2.07 (checkpoints),
`retention-2026-10-09` 1.76, `interaction-retention-2026-10-10` 1.56,
`resume-stage` 0.17.

**`bid-studies`** 0.28 GiB: `2026-11-01-llama1b` 0.26, `2026-11-02-qwen3-4b-development` 0.015.

## Reading the numbers

- The single biggest consumer is Docker data (64.7 GiB) for the 21 SWE-bench
  images the pilot needs; pruning would break the benchmark.
- `bid-models` holds the needed Qwen3-4B and two Llama study GGUFs.
- `grafting-resume` and the study dirs are unique user artifacts.
- Guest cleanup removed ~11.56 GiB total: HF hub 4.67 GiB, installers 1.22 GiB,
  APT archives 5.67 GiB (`apt-get clean` via WSL root).
- `/var` is now 0.50 GiB (was 6.31 GiB).
- Host C: will not grow until a separately approved `fstrim` + compact; the VHD
  is not sparse.

Machine-readable: `WSL_FOLDER_SIZES.json`. Deletion ledger: `WSL_CACHE_CLEANUP.md`.
