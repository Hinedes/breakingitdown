# Change Ledger

## Qwen thinking rerun

- Preserved the frozen `bba5fe8` BID run after Worker 1 exhausted its respawn limit without changing ARGUS.
- Created `beta-gate/qwen-thinking-context` from `bba5fe8`.
- Enabled Qwen thinking, removed `/no_think`, set the default completion cap to 32768, and use Qwen's precise-coding sampling values.
- Discard completed `<think>...</think>` blocks before BID processes model content.
- Added a derived Ollama model with `num_ctx 65536`; the running server was verified with `-c 65536`.
- Added one-second GPU-memory sampling for the new isolated BID candidate. The direct baseline remains unlaunched.
- Stopped the thinking rerun after a protocol failure: the worker repeatedly emitted fenced shell and `END READ`/`END RUN` syntax instead of BID commands, then began a second long respawn after the first bounded stall.
- The complete GPU trace had 2,409 samples: 342 MiB idle, 6,785 MiB peak, and 342 MiB after Ollama automatically unloaded the model.
