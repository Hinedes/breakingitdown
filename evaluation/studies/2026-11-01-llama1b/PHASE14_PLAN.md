# Exploratory phase 14: usable execution and constrained syntax

This follows the preserved epoch-9 all-fail matrix and unsuccessful epochs 10–12 calibration. It is a new treatment, not a replacement or silent rerun of those results. The original 08:00 Taipei cutoff still applies.

- Source: immutable epoch 14, manifest `94847c87c91bf4fc50224c82aa8af879d1e77b8583039bfcab17cafa87f2eb2b`.
- Model: the already-downloaded Llama 3.2 3B Q8_0, same model/settings in every role and paired condition; Q8_0 KV, one GPU slot, target 131072 context if allocation fits.
- New execution condition: rootless namespace/chroot, read-only runtime, only disposable project writable, controls hidden/read-only, no host home/network/credentials, capabilities removed, process-group hard timeout. Bind-mounting the project avoids unsafe copy-back. Default host RUN remains denied.
- New syntax condition: optional llama.cpp GBNF decoding for planning, Worker actions, review, and reconciliation. Worker uses READ, WRITE, sandbox RUN, Done, one action per response. This deliberately differs from unconstrained epoch 9. Both custom Direct and BID receive the same Worker treatment.
- Calibration before study: create `marker.txt` with exactly four bytes `COVE`, no newline, and reach an actual Reviewer verdict and Manager completion. It is not a benchmark cell. All earlier failures remain archived.
- If calibration succeeds, freeze a subsequent source/plan for repeated task comparisons with distinct paired seeds. Do not claim coordination evidence from cells that never reach review/reconciliation. Report baseline-relative oracle gains and role coverage, not just checks already satisfied by starting files.
- Run regression and hostile-boundary checks first. The phase-14 Ubuntu suite passed 185 tests; Windows passed 172 with four unavailable-symlink skips. These are bounded implementation checks, not a kernel security audit.
