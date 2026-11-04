# SWE-bench Verified 20-issue paired pilot

Preparation only. No scored model inference has run. Do not launch the 60-cell
batch without the primary prelaunch review.

## Frozen evidence

| item | value |
| --- | --- |
| dataset | `princeton-nlp/SWE-bench_Verified` revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a` |
| parquet SHA-256 | `a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd` |
| sample | `random.Random(20261102).sample(sorted_ids, 20)` |
| IDs SHA-256 | `5aa9838900afdf9e43b9759975275b6ec5517ff942735364dc6a562c05ac9eed` |
| task inputs SHA-256 | `f6f4b1c473cbbc48f05fb363e170f9587ab04449128a669ba771628d3aaa9286` |
| manifest SHA-256 | see `manifest.json` self-hash |
| evaluator | `SWE-bench/SWE-bench` `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e` (package 5.0.2) |
| task repo | `SWE-bench/swe-bench-tasks` `3d07b464b7b311a0cbfb5ed5b2d8a3b96f84a33d` |
| mini | `SWE-agent/mini-swe-agent` `04d809ceab9df28f9adaed044884180159172930` |
| model | `Qwen3-4B-Instruct-2507-Q8_0`, SHA-256 `260b5b...c19662` |
| llama.cpp | `8642541b255bfb5dc39cd351a178be09013a55d6` |

`PLAN.md` holds the preregistration. `RUNTIME.md` holds the rootless Docker
provenance and the unscored grader sanity evidence. `CONDITIONS.md` records
the three distinct tool interfaces. `manifest.json` is the checked-in
allowlisted evidence; gold patches and test patches live only in the remote
`grading/` directory (mode 0600).

## Code

- `protocol.py`: pure invariants (sampling, schedule, budget, allowlist,
  patch filtering, sentinel, manifest, grade command).
- `freeze.py`: rebuilds the frozen inputs from the parquet and task repo.
  Refuses if the sample no longer matches the frozen ID digest or if the task
  repo disagrees with the dataset's issue/base/patch/test patch/F2P.
- `runner.py`: fresh checkout, exact public issue injection, host BID/Worker
  policy with container-only RUN, offline agent containers, mini controller,
  containerized patch extraction, official predictions, per-cell supervision.
- `proxy.py`: controller-side model proxy; chat-template/tokenizer preflight,
  atomic reserve, fail-closed usage, fixed sampling/seed, JSONL audit + trace.
- `pilot.py`: minimal 60-cell orchestrator: validates manifest, walks frozen
  order, runs one condition, grades with unique run IDs, checkpoints atomically,
  records outcomes/CSV, monitors disk, stops an owned server in `finally`.
- `runtime/`: rootless Docker start/stop scripts.
- `test_protocol.py`, `test_runner.py`, `test_proxy.py`, `test_manifest.py`.

## Checks

```bash
python -m pytest tests evaluation/swebench-pilot -q
python evaluation/swebench-pilot/runner.py --self-test
python evaluation/swebench-pilot/pilot.py --dry-run
```

Remote controller state is under
`/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02`:
`manifest.json`, `frozen/task-inputs.jsonl`,
`frozen/grading/{instances,sanity}.jsonl`, `source/SWE-bench`,
`source/swe-bench-tasks`, `source/mini-swe-agent`, `bid-source`,
`logs/evaluation/...`, `runtime/`.

## Remaining before launch

- Primary review of the manifest hash, schedule, budgets, tool interfaces,
  proxy, and grader sanity. All 20 task images are built and tag-verified.
- No lessons, memory, training, or grader-feedback retries in this pilot.
