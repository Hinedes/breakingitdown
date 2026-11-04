# Development smoke — 2026-11-02

These are bounded product-development checks, not a benchmark, cohort, or
capability claim. Runs `001`–`002` used the ordinary text protocol. Runs
`003`–`016` used the opt-in syntax grammar (`BID_PROTOCOL_GRAMMAR=1`) through
the supported CLI path. `RUN` stayed disabled. Independent checks read final
artifact bytes and persisted TODO/VC state without changing either.

## Task and runtime

Task: create `hello.txt` with exactly `hello from BID` followed by one newline;
create no other project files. Model: Llama 3.2 3B Instruct Q8_0, SHA-256
`b5607b5090a8280063fff2d706bb3408ca6542341b06aab39c3eca0a28575921`;
llama.cpp commit `8642541b255bfb5dc39cd351a178be09013a55d6`; RTX 5070 Ti;
131,072 context, one slot, Q8_0 KV. The endpoint was localhost:8081. The
verified server launch settings were used, with seed 20261124. Each smoke had a
120-second outer deadline; the trace wrapper enforced the stated token ceiling.

## Results

| Run | Source | Limit | Roles (Manager / Worker / Reviewer / reconciliation) | Terminal result |
|---|---|---:|---|---|
| `run-001` | Immutable epoch-20 source, manifest `e925ca9e3d3e6c3c6e9cb6f9be19e04aa9f171bffd74ba4e6299d0e12d20c051` | 30,000 tokens | 1 / 27 / 0 / 0 | `token_cap_prompt`; no final artifact |
| `run-002` | New development copy of epoch 20 plus the stall fix; source-manifest SHA-256 `6f602e032a9161a414cf3466352a1d7835240e808c3b4a7bd63e3102589b1b48` | 100,000 tokens | 1 / 81 / 0 / 0 | `token_cap_prompt`; no final artifact |

In `run-001`, Manager planning added a second “Verify hello.txt exists” task
despite the request for one task. Worker wrote the requested bytes transiently,
then repeatedly emitted unsupported `END READ` and redundant writes. This
reproduced the stall gap: alternating READ/WRITE signatures prevented the
repeat bound from firing, and unchanged writes counted as progress.

In `run-002`, the patched harness marked the first repeated Worker as stalled
and rolled its candidate back. The next Worker output changed into malformed
REPLACE sequences and continued producing non-progressing variants until the
outer smoke token cap. The final TODO remained unchecked; no Task Reviewer or
Manager reconciliation request occurred. `artifact_pass` and `full_loop` were
false. The fix bounded the repeated-turn case; it did not make this model follow
the text protocol or complete the task.

## Regression and mechanism checks

- Before the fix, `test_repeated_multicommand_turn_stalls_and_rolls_back`
  failed: the mock backend had to raise after 12 responses to stop the loop.
  After the fix, it passes and confirms candidate rollback.
- `test_init_to_manager_ratified_artifact` passes through init, Manager plan,
  Worker write, Task Reviewer ACCEPT, Manager ratification, `[x]`, and persisted
  provisional/ratified VC events. It is deterministic harness coverage, not a
  real-model result.
- Windows full suite: **177 passed, 10 capability-specific skips**. Linux full
  suite on the development source copy: **187 passed**.

Commands (Windows paths shown for the local suite):

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP='C:\Users\hind\AppData\Local\Temp\opencode'; $env:TMP=$env:TEMP; $env:TMPDIR=$env:TEMP
& 'C:\Users\hind\AppData\Local\Temp\opencode\bid-audit-venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --basetemp='C:\Users\hind\AppData\Local\Temp\opencode\bid-final-pytest'
```

Linux full-suite command from the development source copy:

```bash
PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$src" TMPDIR="$d/test-tmp" TEMP="$d/test-tmp" TMP="$d/test-tmp" \
  /home/iclab/bid-studies/2026-11-01-llama1b/test-venv-epoch-10/bin/python -m pytest -q -p no:cacheprovider --basetemp="$d/test-tmp/pytest"
```

The patched smoke used `BID_SOURCE_EPOCH=development-working-tree-adapter-stall-fix`,
`BID_SOURCE_MANIFEST="$d/source-manifest.sha256"`,
`BID_STUDY_ROOT="$d"`, the Q8_0 model alias/path above,
`BID_STUDY_CONSTRAINED=0`, `BID_STUDY_SANDBOX_RUN=0`, and
`BID_STUDY_TOKEN_CAP=100000`, then:

```bash
PYTHONPATH="$d/source:$d/source/evaluation/studies/2026-11-01-llama1b" \
  /home/iclab/bid-studies/2026-11-01-llama1b/test-venv-epoch-10/bin/python "$d/smoke.py" "$d/run-002"
```

For `run-001`, the source path was immutable `epoch-20/source`,
`BID_SOURCE_EPOCH=epoch-20`, manifest `epoch-20/SOURCE_MANIFEST.json`, and
token cap 30000. Both used the same task, seed, ordinary prompts, and model
endpoint. The server used the verified `FINAL_RUNTIME_STATE.json` launch
settings with the existing Q8_0 model/KV, context and slot 131072, localhost
port 8081, one slot, 99 GPU layers, and seed 20261124.

## Provenance and cleanup

The development source copy, manifest, smoke script, outcome JSON, and raw
traces are preserved remotely under
`/home/iclab/bid-studies/2026-11-01-llama1b/development-smoke-2026-11-02-epoch20-normal/`.
Run-001 trace SHA-256:
`3a36e5ef2221abacaaf98c0276d3021c07c623faa4e2d6450cb6706f6686571d`.
Run-002 trace SHA-256:
`5c0adfe37e4d8c457a5b5bec2ed05a7802fcf07dfad2d06795373cf2d3078fa0`.
Run-002 outcome SHA-256:
`5afe5332c814103d3c46265c9f82fa0b5d84ebf4fc7907a0653d7fefb57a9507`.
The immutable epoch-20 source, epoch-18/20 queues, and historical traces were
not modified.

The owned llama-server PID 25583 was matched against the expected binary,
model, and port, then stopped with SIGTERM. No llama-server remained; GPU
memory was 390 MiB used / 15,606 MiB free at final verification. No model
downloads or credential changes were made.

## Follow-up CLI smokes — runs 003–016

All runs used the same task, Llama 3.2 3B Instruct Q8_0 model and server
settings, with a one-task provisional batch, `RUN` disabled, and bounded
request/worker deadlines. Individual `outcome.json` files record the complete
source identity and source-manifest SHA-256; raw request/response traces,
workspace events, and final workspace state are preserved in each local
`run-NNN/` directory. Runs `003`–`013` preceded the single-provisional grammar
experiment; `014` recorded a GBNF parse error, `015` fixed the grammar rule
names, and `016` used the state-aware grammar.

| Run | Roles (Manager / Worker / Reviewer / reconciliation) | Artifact check | Result |
|---|---:|---|---|
| `003` | 1 / 1 / 0 / 0 | none | llama.cpp rejected the first Worker grammar |
| `004` | 1 / 13 / 1 / 1 | none | second Worker timed out |
| `005` | 1 / 599 / 1 / 0 | none | Worker timed out after a newline rework |
| `006` | 1 / 6 / 1 / 3 | exact bytes passed | reconciliation returned conflicting Done/Rework |
| `007` | 1 / 11 / 0 / 0 | none | Worker timed out |
| `008` | 1 / 16 / 0 / 0 | none | cycle-stall detection stopped repeated Workers |
| `009` | 1 / 5 / 1 / 3 | two trailing newlines | reconciliation failed |
| `010` | 1 / 5 / 1 / 3 | two trailing newlines | reconciliation failed on project state |
| `011` | 1 / 4 / 1 / 3 | no final newline | reconciliation failed on project state |
| `012` | 1 / 5 / 1 / 3 | no final newline | reconciliation exhausted retries |
| `013` | 1 / 3 / 1 / 3 | no final newline | reconciliation exhausted retries |
| `014` | 1 / 3 / 1 / 3 | no final newline | llama.cpp rejected the first single-provisional grammar |
| `015` | 1 / 3 / 1 / 3 | no final newline | grammar ran; Manager proposed an invalid remaining-plan replacement |
| `016` | 1 / 312 / 1 / 1 | no final newline | Manager added an unsupported task; rework Worker timed out/respawned |

The newline metadata and candidate snapshots let Reviewer and Manager see the
exact artifact state, but did not make their decisions reliable: several
Reviewers accepted candidates with no final newline, and Managers either
contradicted their own task dispositions or added unsupported work. The
state-aware grammar removed conflicting Done/Rework syntax in `016`, but the
model still added a generic task and the follow-up Worker did not recover. No
run reached persisted ratification with the required artifact, so the objective
remains unmet. The requested multi-file/dependency smoke was not run because its
single-file prerequisite never passed.

Latest full-suite checks on the final source state: Windows **184 passed, 10
skipped**; Linux **194 passed**. The later development smokes were preserved
locally under `evaluation/development-smoke/run-003/` through `run-016/`,
including raw traces and outcome/source provenance. No acceptance or completion
was forced, and no immutable study data was changed. The owned llama-server PID
26091 was verified against its expected binary, model, and port, then stopped
with SIGTERM after testing. Port 8081 was closed; GPU memory was 390 MiB used /
15,606 MiB free. No model downloads or credential changes were made.
