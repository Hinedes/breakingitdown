# Llama 3.2 1B BID study report

## Status

- Started 2026-11-01 22:55 Taipei / 14:55Z; hard cutoff 2026-11-02 08:00 Taipei / 00:00Z. The runner checks the cutoff before every cell and will not start a cell within the final 10 minutes.
- Fixed local source baseline is uncommitted tree based on `1399767`; current matrix source is immutable epoch 9 under `/home/iclab/bid-studies/2026-11-01-llama1b/epoch-9/`. No commit made.
- Epoch 8 was stopped after a restart-guard defect; its queue, traces, and outcomes are preserved at `queue-epoch-8.sqlite3`, `runs-epoch-8/`, and `logs/study-epoch8.log`. Do not merge epoch-8 outcomes with the corrected queue.
- Epoch 9 completed all 45 queued outcomes at 2026-11-01 16:48:27Z, before the cutoff guard. Queue has no pending/running cells; runner PID 6314 exited. Immutable source is `epoch-9/`; queue, log, outcomes, and generated summary are `queue-epoch-9.sqlite3`, `logs/study-epoch9.log`, `runs-epoch-9/`, and `summary.json`. Summary SHA-256: `4ebbbe96f5854bc6d8923b8b49b56c55f2da3f04ae35358193508c35f0f5bf30`.
- Epoch-9 source manifest SHA-256: `68143ceb5f63886a99c5f57f2b6c7300cd79f5dbdf056d8a8dcf06bf5a324f1f`; frozen working-tree diff SHA-256: `e9cbe4b3fa0bab9d992f76dc099eefe7ba5d27f8d9fd45017e84ee1f2d692b47` (42 manifested files).

## Runtime and safety

- Model: `bartowski/Llama-3.2-1B-Instruct-GGUF`, revision `067b946cf014b7c697f3654f621d577a3e3afd1c`, F16 file SHA-256 `1f33ad43d2b85b908ff06fe7002b69806a57359b9b2617ca27d7bdea428ae146`; HF Xet object hash `dd7aa72a768af8b93bb5608b71583be5a769b6e9f066beae1332eefa571fa88e` is distinct.
- llama.cpp v0.8.0, commit `8642541b255bfb5dc39cd351a178be09013a55d6`, CUDA 12.8, RTX 5070 Ti. Initial server PID 5182 bound only to `127.0.0.1:8081`; runner restarts only its PID from the pinned launch config when switching contexts. One slot, seed `20261101`, context-shift disabled, F16 K/V, all 17/17 layers offloaded. Main condition reports effective slot context 131072. Memory log: weights 2357 MiB, KV 4096 MiB, compute 192 MiB; roughly 8.5 GiB free after load. 64K is an explicit server restart/memory ablation.
- Client decoding: temperature 0.6, top-p 0.95, top-k 20, min-p 0, repetition penalty 1.0, thinking enabled; 8192 output tokens/request. Per-cell 300s and 50,000 measured prompt+completion tokens. The runner calls llama.cpp's input-token endpoint before each generation, reserves prompt tokens from the aggregate cap, and records any remaining overshoot. Cached prompt tokens remain included in total-token usage.
- `BID_ALLOW_HOST_RUN` is removed and `allow_host_run=False` in both conditions. Search is disabled. Rootless oracle sandbox: user/mount/network/PID namespaces, chrooted Python runtime, copied workspace, tmpfs limits, CPU/address-space/file/process/time limits. Host-home, loopback, and write-isolation probe passed. No HF/cloud credentials are passed to the runner or sandbox.

## Setup smokes (not task-study results)

| Probe | Result |
|---|---|
| Arithmetic, `19 × 23` | `437` |
| BID terminator, strict `Done` | `Done` |
| 32,843-token prompt with two markers | Processed at effective 131072 context in about 1.4s, but first marker was wrong (`B941` vs `A941`); long-context processing is demonstrated, long-context correctness is not. |
| Fixed source Ubuntu suite, package checks | 173 passed; editable install and isolated wheel/prompt load passed. |
| Final epoch-9 Ubuntu suite + study safety/oracle/queue tests | 178 passed. Local Windows suite: 169 passed, 4 skipped. |

The written plan was saved before model setup, but the initial smoke probes preceded freezing the task fixtures and hidden oracles. Those probes used no study task artifacts; this is recorded as a preregistration limitation, not treated as prospective task data.

## Preserved pilot failure

An initial custom-Direct calibration cell ran on epoch 6 before exact prompt-token pre-accounting was added. It is preserved separately at `runs/fbf04a960736473a848095964f5e31f2/`: easy task, D128, repeat 1, 36.641s, 12 Worker responses, 58,960 measured tokens (8,960 over the planned cap), zero of four oracle checks. Repetitive commands exhausted the output budget; generated `RUN` attempts were denied. This is a pilot/tool-budget defect, not a silently rerun or a member of the epoch-8/9 queues. Epoch 9 uses a fresh queue/run namespace and accurate prompt-token preflight.

The first five epoch-8 cells are also preserved as a separate runner validation attempt. All five easy-task outcomes failed the oracle. Three BID variants failed during Manager initialization after three unparseable TODO responses; Direct at both contexts stopped at the corrected prompt-token cap with zero oracle checks. The next cell checkpointed as incomplete because the context-restart guard treated the old server's zombie PID as still live. These are retained protocol/budget/infrastructure outcomes, not task-capability comparisons. Epoch 9 has a fresh queue and fixes only the zombie-state check; no product prompt was tuned after seeing these outputs.

## Comparison conditions

| Condition | Protocol | Context | Batch |
|---|---|---:|---:|
| B128-B4 | BID Manager → Worker → Task Reviewer → Manager reconciliation | 131072 | 4 |
| B128-B1 | Same BID roles | 131072 | 1 |
| B64-B4 | Same BID roles, context/memory ablation | 65536 | 4 |
| D128 | Custom single `WorkerAdapter`, no Manager or Reviewer | 131072 | n/a |
| D64 | Same custom Direct protocol | 65536 | n/a |

BID uses one shared model backend sequentially, with fresh role conversations; no weights are loaded per role. Worker history resets between tasks. `D128` is a shared paired reference for both 128K BID variants; `D64` is shared for the 64K pair. The Direct condition is not an as-is coding agent and does not settle the native-agent comparison.

## Task-cell results

Populate from the final `summary.json` and immutable per-run `outcome.json` files. Report exact n, binary successes, partial oracle checks, time/tokens, termination classes, and paired wins/ties/losses. Do not claim significance or combine the epoch-6 pilot, epoch-8 validation attempt, and epoch-9 matrix.

Each entry is binary passes out of 3, aggregate partial checks passed/available, median measured tokens, and median wall seconds.

| Condition | Easy | Pipeline | Repo fix |
|---|---|---|---|
| B128-B4 | 0/3; 0/12; 1,896; 1.375s | 0/3; 3/21; 41,837; 21.967s | 0/3; 6/9; 49,864; 5.232s |
| B128-B1 | 0/3; 0/12; 1,896; 1.198s | 0/3; 3/21; 41,837; 22.224s | 0/3; 6/9; 49,864; 4.858s |
| B64-B4 | 0/3; 0/12; 1,572; 0.828s | 0/3; 3/21; 46,519; 11.709s | 0/3; 6/9; 49,864; 5.214s |
| D128 (shared) | 0/3; 0/12; 49,100; 32.257s | 0/3; 3/21; 40,810; 12.033s | 0/3; 6/9; 50,000; 87.470s |
| D64 (shared) | 0/3; 0/12; 47,579; 30.309s | 0/3; 3/21; 40,810; 11.905s | 0/3; 6/9; 50,000; 87.571s |

**Outcome:** 0/45 binary passes; 45/210 individual oracle checks passed. All 27 paired comparisons tied on binary pass/fail (all failed); no BID-only or Direct-only pass. Each of the 15 task-condition cells has n=3. Terminations: 9 normal-status harness errors, 30 `token_cap_prompt`, 6 `token_cap`; no wall caps, missing usage, or token overshoots. All nine BID easy cells failed Manager initialization after three invalid TODO attempts. All 45 cells failed binary scoring. Across Worker traces, 708 RUN commands were attempted and denied; zero ran. There were 36 Worker sessions and no Task Reviewer verdicts or Manager reconciliation decisions.

Every final task-artifact tree exactly matched its seed: no task files changed,
were added, or removed. The 45 partial checks were satisfied by seeded files
and tests, not model-produced improvements. Recomputed response-sequence
hashes give 25 distinct sequences across the 15 task-condition groups and 17
distinct sequences across all 45 outcomes. The same server seed `20261101` was
used for every repeat, so these are not independent replications. Maximum
preflight prompt length was 17,837 tokens, far below both allocated contexts;
64K/128K was not exercised. The BID batch-size condition was not reached.

**Paired references:** D128 was shared for the two 128K BID conditions; D64 was shared for B64-B4. The 27 task/repeat pairs are descriptive repeats under this fixed seed, not independent Direct runs and not inferential statistics.

**Final machine state at 2026-11-01 16:53:57Z:** runner 6314 exited; llama-server PID 7318 remains bound to `127.0.0.1:8081`, Llama 3.2 1B, one idle slot at 65536 context. RTX 5070 Ti reported 5,224 MiB used, 10,772 MiB free, 0% utilization. Server was left running; no other process was stopped.

## Limitations

- Only a 1B model, three synthetic tasks, and a custom single-Worker baseline are covered. This is not evidence against coding agents as-is.
- The 32K marker failure shows input processing is not equivalent to reliable retrieval. No correct full-window 128K canary was established.
- Historical manifests for the 72cfea4 and f2ae938 rehearsal archives verify (126/126 and 350/350 entries). The `benchmark-v2/hidden_oracle.sha256` sidecar disagrees with its adjacent oracle (`bc704f…` expected, `6b6105…` computed); no archive files were changed, and historical oracle replay remains pending. This new study does not validate the historical archive.
- This epoch does not compare architecture behavior: all BID easy cells failed
  planning, the full Reviewer/reconciliation loop was not reached, and no task
  files changed. The fixed seed and three synthetic tasks do not support broad
  or statistical claims.

**Interpretation correction:** epoch 9 is a protocol floor and method
confound, not an architectural negative. The Manager failed to initialize all
nine easy BID cells; no cell exercised Reviewer verdict plus Manager
reconciliation. There was no task-file progress, and the partial checks came
from seed artifacts. Therefore the batch/context contrasts were allocated but
not reached, and these results do not compare architecture behavior. Direct
remains a custom single-Worker protocol, not an as-is coding agent. This shows
only that this specific 1B setup did not complete the frozen tasks under this
protocol.

The immutable epoch manifest is correctly resolved from the archived source
layout. All 45 outcomes link to SHA-256
`68143ceb5f63886a99c5f57f2b6c7300cd79f5dbdf056d8a8dcf06bf5a324f1f`; its
byte-identical copy is `SOURCE_MANIFEST.epoch-9.json` in this local folder.
