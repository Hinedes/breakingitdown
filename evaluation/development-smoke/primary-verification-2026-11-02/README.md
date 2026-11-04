# Independent primary verification, 2026-11-02

This is a separate ordinary-CLI verification of the corrected source, not a
revision of the earlier Qwen smoke runs.

- Source snapshot: `/home/iclab/bid-studies/2026-11-01-llama1b/development-primary-verify-2026-11-02/source`
- Source manifest SHA-256: `f4e87c40701c449c9b1f6afc35fb7aa99502e2839882257d204a8b6264e72c2a`
- Model/runtime: Qwen3-4B-Instruct-2507 Q8_0, one localhost:8081 server, 65,536 context, Q8_0 KV, seed `20261125`. Batch 1, grammar off, host RUN off.
- Task: create `arithmetic.py` with `double(value)` and `main.py` importing it and printing `double(21)`. CLI `init` and `run` both exited 0; Manager 1, Worker 4, Reviewer 2, reconciliation 3. Both TODO tasks are `[x]` and ratified.
- Independent generated-code checks ran only through the existing `run_python` isolated executor on a copied workspace, not host `RUN`: `double(-2, 0, 5)` returned `[-4, 0, 10]`; `main.py` exited 0 and printed `42\n`.
- Broad Linux product suite on this source: **195 passed in 2.83s**. The owned server stopped in `finally`; afterward the GPU reported 390 MiB used and 15,606 MiB free.

This verifies the bounded end-to-end path for this task. It does not establish
general task reliability or an architecture benefit. See `outcome.json`,
`source-manifest.json`, `workspace-events/events.jsonl`, `workspace/`,
`tests.txt`, `launch.json`, and `server.log` for the recorded evidence.
