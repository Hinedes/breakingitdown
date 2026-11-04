# WSL cache cleanup (user-authorized) and folder sizes

Date: 2026-11-02. Three approved groups were targeted; two were deleted, one was
blocked by permissions. No models, checkpoints, pilot artifacts or study data
were touched. Machine-readable: `WSL_CACHE_CLEANUP.json`, `WSL_FOLDER_SIZES.json`.

## Deleted (guest side)

| group | path | bytes | notes |
| --- | --- | --- | --- |
| HF hub download cache | `~/.cache/huggingface/hub` | **5,011,990,323** | 4 repo caches (Llama-3.2-1B 4.95 GB, glue 57 MB, pythia 8 KB, `.locks`); token, stored_tokens, `.agent_harnesses.json`, `datasets/`, `xet/`, `version.txt` preserved |
| Anaconda installer | `~/Anaconda3-2023.09-0-Linux-x86_64.sh` | 1,153,404,010 | regular `.sh`, size verified |
| Miniconda installer | `~/Miniconda3-latest-Linux-x86_64.sh` | 162,067,098 | regular `.sh`, size verified |

**Total deleted: 6,327,461,431 B (~5.89 GiB).**

## APT cache — initially blocked, then completed via WSL root

The first attempt was blocked (`sudo -n` unavailable, root-owned directory). Per
the primary, the same approved cleanup was executed through the normal WSL root
account:

```
wsl -d Ubuntu-22.04 -u root -- /usr/bin/apt-get clean   # rc=0
```

Preflight: `uid=0(root)`, `/var/cache/apt/archives` canonical `root:root`, no
active apt/dpkg process, zero-length lock files untouched. Result:

| measure | before | after |
| --- | --- | --- |
| `.deb` files | 126 | **0** |
| archives bytes | 6,088,473,504 | 24,576 |
| guest `used` | 175,900,397,568 | 169,662,173,184 |
| guest `available` | 79,766,409,216 | 86,004,633,600 |
| Windows C: free | 5,705,830,400 | 5,694,988,288 |

**APT deleted: 6,088,448,928 B (~5.67 GiB).**

### Three-group total (guest)

| group | bytes |
| --- | --- |
| HF hub cache | 5,011,990,323 |
| Installers | 1,315,471,108 |
| APT archives | 6,088,448,928 |
| **total** | **12,415,910,359 (~11.56 GiB)** |

Guest `used` fell from 182,227,968,000 to 169,662,173,184 (−11.70 GiB). Host C:
is still ~5.3 GB free; a separately approved `fstrim` + compact is still required
to return guest space to the host.

## Blocked (historical, now resolved)

| group | path | bytes | reason |
| --- | --- | --- | --- |
| APT archives | `/var/cache/apt/archives` (126 `.deb`) | 6,088,473,504 | first attempt: `sudo -n` unavailable; directory `root:root 755`. Resolved via WSL root `apt-get clean` (above). |

## Guest vs host

| metric | before | after |
| --- | --- | --- |
| guest `used` | 182,227,968,000 | 175,900,397,568 |
| guest `available` | 73,438,838,784 | 79,766,409,216 |
| Windows C: free | 5,735,329,792 | 5,726,121,984 |

Guest gained ~6.33 GB; **host C: did not change**. Freeing guest bytes returns to
the host only after a separately approved `fstrim` + compact (the VHD is not
sparse). Compact is still needed.

## Folder rundown (`du -x -B1`, allocated bytes, GiB = bytes/2^30)

Top level of `/` (depth 1; nested rows are not additive):

| path | bytes | GiB |
| --- | --- | --- |
| `/home` | 142,811,942,912 | 133.0 |
| `/usr` | 17,796,538,368 | 16.6 |
| `/var` | 6,775,742,464 | 6.31 |
| `/opt` | 2,172,547,072 | 2.02 |
| `/tmp` | 1,357,959,168 | 1.26 |
| `/lost+found` | 16,384 | 0.00 |
| `/` (total) | 170,942,840,832 | 159.2 |

`/home` depth 1: `/home/iclab` 142,811,934,720 (133.0 GiB); `/home/che` 4,096.

### `/home/iclab` depth 1 (top 27)

| path | GiB | note |
| --- | --- | --- |
| `.local` | 73.7 | drill below |
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
| `.cache` | 0.39 | after HF cleanup |
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

### Drill-downs (depth 1 of each)

`.local` 79,163,289,600 (73.7 GiB): `share` 70,506,631,168 (65.7), `lib`
8,540,647,424 (7.95), `bin` 81,797,120, `usr` 34,091,008.
`.local/share` 70,506,631,168: **`docker` 69,458,313,216 (64.7 GiB, 21 SWE-bench
images + data — protected / no prune)**, `uv` 522,244,096, `opencode` 519,786,496.

`bid-benchmarks/swebench-verified20-2026-11-02` 6,077,661,184 (5.66 GiB):
`runs-export-v2` 4,021,346,304 (3.75), `mini-venv` 732,921,856, `runs`
512,045,056, `venv` 472,956,928, `source` 178,200,576, `logs` 134,455,296,
`data` 20,549,632, `bid-source-c637…` 487,424, `frozen` 499,712.

`grafting-resume` 13,764,595,712 (12.8 GiB): `.venv312` 7,652,175,872,
`gate-repro` 2,217,525,248 (checkpoints/evidence), `retention-2026-10-09`
1,892,171,776, `interaction-retention-2026-10-10` 1,676,390,400, `resume-stage`
182,935,552.

`bid-studies` 296,787,968 (0.28 GiB): `2026-11-01-llama1b` 280,174,592,
`2026-11-02-qwen3-4b-development` 16,609,280.

## Still protected / not deleted

`bid-models` GGUFs, `grafting-resume` checkpoints/evidence, `bid-studies`,
`bid-benchmarks` (runs, images, frozen rows, provenance), `BreakItDown`,
`bid-eval`, venvs, `.local/share/uv`, `.local/lib`, Docker data/images.

## Next step

Host space is unchanged. A `fstrim` + compact of the vhdx (WSL shut down or
`wsl --manage … --set-sparse`) is required to return guest free space to C:; that
was **not** performed and needs separate approval. The APT cache remains the
largest easy guest-only candidate (6.09 GB) once sudo is available.
