# Llama 3.2 1B BID study plan

**Started:** 2026-11-01 22:55:16.9776131 Taipei (UTC+08:00) / 14:55:16.9776131Z  
**Hard cutoff:** 2026-11-02 08:00 Taipei / 00:00Z. No new run starts at or after cutoff; stop/checkpoint bounded work by cutoff.  
**Source:** current fixed, uncommitted local tree based on `1399767`; source snapshots are immutable epochs under `/home/iclab/bid-studies/2026-11-01-llama1b/epoch-N/`, each with a per-file SHA-256 manifest and `git diff HEAD` hash. Epoch 1 is the fixed product tree; subsequent epochs add only the frozen tasks/oracles/runner and correct study-tool defects. Exclude `.git`, caches, and credentials. Do not use the distinct remote branch as the tested source.

**Preregistration caveat:** the written plan preceded model setup, but the first arithmetic/protocol/long-context setup smokes happened before task fixtures and the hidden oracle were frozen. These were not task-condition runs and did not use task artifacts, but this is not a fully prospective preregistration. The task-cell matrix, task text, scoring checks, and source epoch are frozen before any queued task-cell run; the setup-smoke outputs remain separately recorded and are not folded into study results.

## Frozen model/runtime

- `bartowski/Llama-3.2-1B-Instruct-GGUF`, revision `067b946cf014b7c697f3654f621d577a3e3afd1c`, `Llama-3.2-1B-Instruct-f16.gguf`; pin and verify the file SHA-256 before inference.
- File SHA-256 `1f33ad43d2b85b908ff06fe7002b69806a57359b9b2617ca27d7bdea428ae146` (HF file metadata); Xet object hash `dd7aa72a768af8b93bb5608b71583be5a769b6e9f066beae1332eefa571fa88e` is recorded separately and is not the file SHA-256.
- llama.cpp tag `v0.8.0`, commit `8642541b255bfb5dc39cd351a178be09013a55d6`; CUDA 12.8, CMake 3.22.1, Ninja 1.10.1, `GGML_CUDA=ON`, `GGML_CUDA_FA=ON`, `CMAKE_CUDA_ARCHITECTURES=120` (CMake selects 120a), Release, host GCC 11.4. RTX 5070 Ti, one active model, localhost only, one parallel slot, native context only, context shifting off, F16 K/V cache, all 17/17 layers offloaded. Main context target 131072; the 65536 condition is an explicit memory ablation.
- Model payload/settings are shared: temperature 0.6, top-p 0.95, top-k 20, min-p 0, repetition penalty 1.0, thinking enabled, per-request output cap 8192. No search, no RUN, and no external services. BID's worker continuity remains reset (the fixed tree has no continuous-history option); set `BID_WORKER_CONTINUITY=reset` and `BID_REPO_CONTEXT=0` in launch environments.

## Conditions and budget

| ID | Runner | Context | BID provisional batch |
|---|---|---:|---:|
| B128-B4 | BID full roles | 131072 | 4 |
| B128-B1 | BID full roles, batch ablation | 131072 | 1 |
| B64-B4 | BID full roles, context/memory ablation | 65536 | 4 |
| D128 | custom single `WorkerAdapter` session, no Manager or Reviewer | 131072 | n/a |
| D64 | same custom Direct protocol | 65536 | n/a |

This is a custom single-Worker comparator, **not** an as-is coding-agent result. D128 is shared by the two 128K BID pairs and D64 by the 64K pair; shared references are counted once, not as independent runs. Each task/condition targets 3 repeats. Within each fixed-context block, paired condition order is shuffled by schedule seed `20261101`; the queue runs the 128K block before 64K to avoid repeated model reloads. Each run is capped at 300 seconds, 50000 accounted prompt+completion tokens total, and 8192 generated tokens per request; every role, retry, and reconciliation request counts. Usage is read from the server response; prompt-token uncertainty can cause a recorded cap overshoot. Any missing usage, budget-boundary overshoot, early context failure, or time cap is retained and marked, never silently rerun or dropped. BID and its paired Direct use the same per-run limits.

## Tasks and oracles

Freeze three levels before model access: (1) a small single-module function task for protocol calibration; (2) a dependent 3–5-module pipeline with explicit interfaces and sample input; (3) a small existing-repository bug-fix fixture. Use byte-identical seeded workspaces and separate hidden deterministic oracles that verify required files and behavior, not BID status. Hash task, seed workspace, oracle, source, and final artifacts. Preserve all attempts and partial checks. The model never receives oracle code/results before termination.

**Execution gate:** RUN is denied in both compared conditions. Oracle execution uses a rootless user/mount/network/PID namespace, chroot into a temporary root containing only a Python runtime and a copy of the task workspace, tmpfs-only writable locations, and CPU/memory/process/file/time limits. A sandbox probe must verify the host home is absent, host loopback is unreachable, and writes stay in the isolated copy before generated code is run.

## Required record and stop rules

Save a preregistration copy, source/task/oracle hashes, exact server and safe client settings, server effective context/KV/parallel/GPU allocation, peak VRAM/RAM, seeded run order, request-level token/timing/role counts, process termination reason, raw prompt/response traces without auth headers/tokens, final workspace hashes, oracle output, and all failures. Store raw traces on the remote study volume; copy concise portable summaries and this ledger update locally. No claim of statistical significance, horizon impossibility, or native-agent parity from this study. Honor the 08:00 Taipei cutoff even if the planned matrix is incomplete.
