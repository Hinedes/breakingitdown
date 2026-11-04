# Epoch 13 future-runner methodology

This is a runner-only source epoch. No model condition or benchmark cell was
run because epochs 10-12 did not establish strict Manager → Worker → Reviewer
→ Manager coverage.

- Each cell runs in a child process with its own POSIX process group. The
  parent enforces a hard `WALL_CAP + 30s` timeout, terminates then kills the
  entire group, verifies cleanup, and checkpoints partial trace/workspace
  hashes if the child exits without an outcome.
- Server/model context preparation happens before the child timer and is
  recorded as `setup_s`; inference wall and token budgets are cell-local.
- Default cell budget is 100,000 accounted prompt plus completion tokens,
  300 seconds, and 8,192 generated tokens/request. These values apply equally
  to each paired condition; they are not a claim that future conditions ran.
- Repeat-specific seeds are `SEED + repeat`, included in each generation and
  token-preflight payload and recorded in traces/outcomes. Cached prompt tokens
  are reported separately from logical prompt and completion totals.
- Model path, alias, SHA-256, and K/V cache type are explicit runtime
  parameters. Each completed outcome resolves and hashes the immutable source
  manifest from the nearest source-epoch ancestor.

Validation covers the POSIX process-group timeout and descendant cleanup. A
future cohort still requires successful strict-protocol calibration before any
architecture, batch, context, or RUN-enabled condition is scheduled.
