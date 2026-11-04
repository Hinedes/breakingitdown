# Exploratory phase 17: exact artifact evidence

Phase 16 produced a correct marker and reached review and reconciliation, but the Manager falsely claimed it had a newline and requested rework. Its optional Add section then repeated until truncation. Preserve that calibration unchanged.

Root-cause fix: review trees preserve raw newline characters and changed-file evidence states the exact candidate UTF-8 byte count and final-newline kind. Previously text-mode reads normalized CRLF, and line-based diffs did not distinguish final-newline-only changes. This metadata is artifact evidence, not a hidden-oracle verdict.

The constrained study protocol also bounds each rationale/description to 512 characters and Add/Replace to four entries per decision. These are declared exploratory transport limits; original BID remains unconstrained by default. The model still decides acceptance/rework/continuation and no expected solution is supplied.

Freeze epoch 17 and validate it before model access. First rerun the four-letter marker calibration with a new seed. If artifact correctness and review/reconciliation coverage are established, run the frozen three-task, five-condition matrix with three distinct paired seeds, 100,000 logical tokens and 300 seconds per condition, sandbox RUN and constrained decoding identical for Direct and BID. Score actual artifacts independently even if BID falsely rejects or fails to report completion. Allocation is 128K versus 64K, with one resident 3B Q8_0 model and Q8_0 cache. Report maximum exercised prompt, seed-baseline-relative gains, all failures and shared Direct references; do not call this an as-is native-agent comparison. Original cutoff: 08:00 Taipei.
