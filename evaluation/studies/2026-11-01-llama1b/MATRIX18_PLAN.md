# Prospective epoch-18 task matrix

The marker calibration has an independently correct artifact and real Worker, Reviewer, and reconciliation calls. BID still falsely requests Manager rework and reaches its configured limit; do not hide this failure or force model acceptance. Full harness completion is a separate outcome from artifact correctness.

Freeze the following cohort before any epoch-18 benchmark task call:

- Source epoch 18: `ceb88fb7f2390dfb862e34445f03de9a9080935ecea471a63e69c795e4ce629a`; Ubuntu regression plus study suite: 189 passed.
- Llama 3.2 3B Instruct Q8_0 weights SHA-256 `b5607b5090a8280063fff2d706bb3408ca6542341b06aab39c3eca0a28575921`, Q8_0 K/V, single GPU slot. Main effective context 131072 (12,194 MiB used, 3,802 MiB free); compare 65536 separately.
- Same frozen easy, pipeline, and repo-fix tasks and independent oracles as epoch 9. Evaluate and subtract the seed workspace baseline checks when interpreting progress.
- Conditions: BID 128K/batch4, BID 128K/batch1, BID 64K/batch4, custom Direct 128K, custom Direct 64K. Three repeats with explicit seeds 20261102, 20261103, 20261104. Shared Direct references are not independent duplicate runs.
- Same constrained text protocol, concise study role instructions, bounded state-valid reconciliation grammar, and persistent rootless sandbox RUN for both compared Worker implementations. Original default production prompts/protocol remain distinct; this is an exploratory transport treatment, not a native-agent benchmark.
- Equal per-condition budget: 100,000 logical prompt-plus-completion tokens, 300 seconds plus the documented 30-second supervisor cleanup allowance, and at most 8192 generated tokens/request. Count every BID role/retry and cached prompt tokens; also report cached totals separately.
- Seeded shuffled paired order within context blocks; all 128K cells precede the 64K block. Each server restart is shared setup, not silently charged to one treatment.
- Preserve traces, outcomes, setup costs, maximum exercised prompt, failed statuses, final-artifact hashes and baseline-relative oracle checks. Do not reinterpret a false-positive review or false-negative completion as a successful artifact.
- No changes to this cohort after outcomes are observed. Follow-ups require a new epoch/condition. Stop launching work within ten minutes of the original 08:00 Taipei cutoff.
