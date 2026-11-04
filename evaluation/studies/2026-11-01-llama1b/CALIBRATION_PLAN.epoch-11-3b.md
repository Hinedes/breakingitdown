# Epoch 11 Llama 3.2 3B strict-protocol calibration

**Reason:** the separate 1B epoch-10 calibration initialized a Manager TODO
made entirely of generic `Description` placeholders; the Worker then looped on
unrelated paths and never reached Review. This cohort follows a general
placeholder-validation and prompt correction and uses one larger model. It is
protocol calibration only, not an architecture or benchmark comparison.

**Unseen task:** `calibration-epoch-11-3b/TASK.md`. The expected exact file
bytes are checked only after the role loop ends and are not in the Worker
workspace or mounted oracle. Only one BID project runs. Acceptance requires a
valid Manager plan, a Worker file edit, a Reviewer verdict, a Manager
reconciliation decision, and an exact hidden-check pass.

**Model:** `bartowski/Llama-3.2-3B-Instruct-GGUF`, repository revision
`5ab33fa94d1d04e903623ae72c95d1696f09f9e8`, file
`Llama-3.2-3B-Instruct-Q8_0.gguf`, expected file SHA-256
`b5607b5090a8280063fff2d706bb3408ca6542341b06aab39c3eca0a28575921`.
Quantization is Q8_0 weights with Q8_0 K/V cache. Effective context is 65,536,
one slot, localhost only. This choice leaves materially more VRAM headroom
than combining 3B F16 weights and F16 KV at 64K on this 16 GiB GPU. Record
actual model metadata and GPU allocation in the outcome.

**Decode and supervision:** one request seed, `20261103`, explicitly included
in every generation and token-preflight payload. Temperature 0.6, top-p 0.95,
top-k 20, min-p 0, repetition penalty 1.0, thinking enabled, 8,192 output
tokens/request, 30,000 total accounted tokens, and 300 seconds hard wall time.
An external supervisor owns a new process group and kills it on timeout; do not
start another model request until it exits. RUN and SEARCH remain unavailable.

**Boundary:** source, model, run folder, plan, and queue are unique to epoch 11.
Epochs 9 and 10 remain unchanged. A failed 3B calibration ends the
architecture work for this window; no benchmark matrix, batch/context
ablation, or model substitution follows.
