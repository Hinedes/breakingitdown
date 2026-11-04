# All-20 gold/base gate results - pre-model metadata

Date: 2026-11-02. Run stamp `0e59d6ea`. No model was started and no scored cell
was run (n = 0 scored cells). These gates are preparation metadata: they are
recorded outside every agent input and are not task or prompt content.

## Headline

| measure | value |
| --- | --- |
| runs | 40 (20 gold + 20 base) |
| gold submitted / completed / resolved | 20 / 20 / **20** |
| base submitted / completed / resolved | 20 / 20 / **1** (`django__django-10097`) |
| base not resolved | **19** |
| discriminators (gold PASS, base FAIL) | **19** |
| base-already-official-pass (blind, kept) | **1** `django__django-10097` |
| unexpected | 0 |
| non-zero exits | 0 |
| infrastructure failures / errors | 0 / 0 |
| ambiguous base failures | 2 (see below) |
| empty patches / unstopped containers | 0 / 0 |
| P2P failures in any arm | 0 |
| wall | 1272.6 s of instance runs, 21.2 min job |

Raw official metric keeps all 20 visible, including the blind case. The
discriminator subset is the 19 gold-PASS/base-FAIL issues; only those can show
that a condition did something.

## Files

- `gates.json` - per-issue gold/base records: run ID, submitted/completed/
  resolved/unresolved, infra/error/ambiguous/empty-patch counts, patch-applied
  flag, F2P and P2P success/failure counts, classification, plus the summary and
  eligibility lists below.
- `gates.csv` - the same per-issue fields in one row per issue.
- `GATE_RESULTS.md` - this report.

Aggregate counts only: no gold patch, no test patch, no expected test names and
no hidden rows are exported. Full official logs and reports stay controller-side
under `logs/evaluation/gate-{gold,base}-<instance>-0e59d6ea/`.

## Method

- Frozen hidden rows `frozen/grading/instances.jsonl`, one instance per run,
  `--max_workers 1 --timeout 1800`, all images prebuilt.
- Gold arm: `--predictions_path gold`.
- Base sentinel: `model_name_or_path = gate-base`, patch adds only
  `.bid-empty-patch`.
- Fresh, previously unused run IDs per arm and instance; no container or cache
  reused (zero containers afterwards). A first invocation was rejected by
  argparse for an unsupported flag and produced no runs; its 20 stale prediction
  files were removed.

## Eligibility list (fixed before any model outcome)

Discriminator, 19: `django__django-11133`, `django__django-11820`,
`django__django-12325`, `django__django-13212`, `django__django-15499`,
`django__django-15851`, `django__django-15973`, `django__django-16662`,
`matplotlib__matplotlib-23314`, `psf__requests-1142`,
`pydata__xarray-6938`, `pylint-dev__pylint-4661`,
`pylint-dev__pylint-7277`, `pytest-dev__pytest-10356`,
`sphinx-doc__sphinx-7454`, `sphinx-doc__sphinx-7757`,
`sympy__sympy-12489`, `sympy__sympy-20916`, `sympy__sympy-24213`.

Blind but kept, 1: `django__django-10097` (official metric already passes on an
empty base patch; this is **not** evidence that the base code is correct - see
`../django-preflight-2026-11-02/ROOT_CAUSE.md`). It stays in the frozen 20 with
an explicit metric-blind label and is never counted as evidence that a condition
solved the issue.

No issue was replaced, dropped or re-pointed.

## Ambiguous base failures (reported, not excluded)

Two base runs carry a harness `ambiguous_failure` flag. Neither is an
infrastructure failure or error, both are submitted/completed and not resolved,
and both keep their gold-PASS/base-FAIL classification:

| instance | base failure reason | gold |
| --- | --- | --- |
| pylint-dev__pylint-4661 | `missing_module` | resolved, f2p 1/1 |
| pytest-dev__pytest-10356 | `no_tests_collected` | resolved, f2p 1/1 |

Both failure modes are collection/import level at base (the test needs an API the
gold patch introduces), not assertion-level behavioural failures. They remain
eligible by the stated rule, and are listed here so the classification is not
taken as an unqualified behavioural demonstration.

## Per-issue

| instance | gold resolved | base resolved | base f2p fail | classification |
| --- | --- | --- | --- | --- |
| django__django-10097 | yes (f2p 438, p2p 1432) | **yes** (438/1432) | 0 | base-already-official-pass (blind) |
| django__django-11133 | yes | no | 1/1 | gold-PASS / base-FAIL |
| django__django-11820 | yes | no | 2/2 | gold-PASS / base-FAIL |
| django__django-12325 | yes | no | 2/2 | gold-PASS / base-FAIL |
| django__django-13212 | yes | no | 5/5 | gold-PASS / base-FAIL |
| django__django-15499 | yes | no | 1/1 | gold-PASS / base-FAIL |
| django__django-15851 | yes | no | 1/1 | gold-PASS / base-FAIL |
| django__django-15973 | yes | no | 1/1 | gold-PASS / base-FAIL |
| django__django-16662 | yes | no | 1/1 | gold-PASS / base-FAIL |
| matplotlib__matplotlib-23314 | yes | no | 1/1 | gold-PASS / base-FAIL |
| psf__requests-1142 | yes | no | 1/1 | gold-PASS / base-FAIL |
| pydata__xarray-6938 | yes | no | 1/1 | gold-PASS / base-FAIL |
| pylint-dev__pylint-4661 | yes | no | 1/1 | gold-PASS / base-FAIL (ambiguous: missing_module) |
| pylint-dev__pylint-7277 | yes | no | 1/1 | gold-PASS / base-FAIL |
| pytest-dev__pytest-10356 | yes | no | 1/1 | gold-PASS / base-FAIL (ambiguous: no_tests_collected) |
| sphinx-doc__sphinx-7454 | yes | no | 1/1 | gold-PASS / base-FAIL |
| sphinx-doc__sphinx-7757 | yes | no | 1/1 | gold-PASS / base-FAIL |
| sympy__sympy-12489 | yes | no | 1/1 | gold-PASS / base-FAIL |
| sympy__sympy-20916 | yes | no | 1/1 | gold-PASS / base-FAIL |
| sympy__sympy-24213 | yes | no | 1/1 | gold-PASS / base-FAIL |

On every discriminator the base sentinel fails exactly the declared F2P list and
P2P is 100 % in both arms.

## Pins in force for these gates

| pin | value |
| --- | --- |
| source `bid_source_sha256` | `f4999e9656a553321af77c2111340ac80c6899be1afe84a1e0d708d31a828f23` |
| manifest `manifest_sha256` | `9e662f1174352ffe349fd9e14ef0957aa8bee4308e3c502fcc56d572c995f0c0` |
| superseded source / manifest | `74416f0b...20c94` / `b2fca9af...ae48ea` (`../django-preflight-2026-11-02/PREP_PROVENANCE.md`) |
| frozen 20 IDs `ids_sha256` | `5aa9838900afdf9e43b9759975275b6ec5517ff942735364dc6a562c05ac9eed` |
| public task inputs | `f6f4b1c473cbbc48f05fb363e170f9587ab04449128a669ba771628d3aaa9286` |
| hidden grading rows | `3e9db0c781c6a163352e4493d39eb23c5176890fcff33ee94c00aeea56bc66b1` |
| sanity row | `6c0360a623986c9a5847279e5366adf34d5ec284d0469279e999e64c664715fe` |
| evaluator / task repo | `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e` / `3d07b464b7b311a0cbfb5ed5b2d8a3b96f84a33d` |

## Machine state after the sweep

0 containers and 0 shims; 21 SWE-bench images retained; rootless dockerd pid
3074; no llama-server, proxy, agent or mini process and no listeners on
8081/1809; no `runs/` directory; 40 gate log dirs plus the earlier preflight
records, 58 MB total; 73 GiB free on `/home/iclab`. Nothing staged, committed or
pushed; the user repo and archives are untouched.
