# Preparation provenance: superseded and prospective identities

Date: 2026-11-02. Recorded before any scored model inference (n = 0 scored cells).

## Superseded (preflight-failed preparation)

| item | value |
| --- | --- |
| source identity `bid_source_sha256` | `74416f0b2f290b99f0af1f9b9110cf6878dcc59a57d91c7aa245e2131fb20c94` |
| manifest `manifest_sha256` | `b2fca9aff781565bc615b866e7f2157432a1c6b88ca11c8694dddfdc57ae48ea` |
| source file count | 26 |

This identity is the one that ran the failed first-issue gate. Its gate records
are preserved unchanged:

- `PREFLIGHT_FAILURE.md` (aggregate only)
- `manifest-74416f0b.json` (the exact superseded manifest; reseal reproduces
  `b2fca9af...ae48ea`)
- remote `logs/evaluation/preflight-gold-django10097-9abb9561/`
- remote `logs/evaluation/preflight-base-django10097-9abb9561/`

## Why a new identity

`evaluation/swebench-pilot/test_grading_coverage.py` was added after the freeze
to pin the `django__django-10097` grading mechanism. It lives inside the
`bid/, prompts/, pyproject.toml, evaluation/swebench-pilot/*.py` source
fingerprint scope, so the live source tree no longer matches `74416f0b...`.
The earlier statement that no new source identity was needed was wrong for that
reason alone; no `bid/`, `prompts/`, `pyproject.toml` or other pilot `.py` file
changed.

## Prospective

| item | value |
| --- | --- |
| source identity `bid_source_sha256` | `f4999e9656a553321af77c2111340ac80c6899be1afe84a1e0d708d31a828f23` |
| manifest `manifest_sha256` | `9e662f1174352ffe349fd9e14ef0957aa8bee4308e3c502fcc56d572c995f0c0` |
| source file count | 27 |

`manifest.json` now pins the prospective source hash and records the superseded
pair under `pins.superseded_bid_source_sha256` / `pins.superseded_manifest_sha256`.
Both values were recomputed with the real enforcement function
(`pilot.source_tree_hash`) and the manifest was resealed with
`protocol.seal_manifest`; `test_manifest.py` passes (3 passed).

Unchanged by this reseal: the frozen 20 IDs and their hash, base commits, problem
statements, generation seeds, condition order, budgets, dataset/parquet pins,
evaluator/task-repo/mini/model pins, and the hidden grading rows.

## Gate classification is pre-model metadata

The all-20 gold/base gate results are controller-side preparation metadata. They
are recorded outside any agent input: no agent prompt, mount, workspace or
container receives expected test names, gate labels, gold/test patches, or hidden
rows. Nothing in the gate records is user-facing prompt or task text.

## Analysis rules fixed before model outcomes

- Report raw official `resolved` for all 20, including cases whose metric is
  blind (an empty patch resolves).
- Separately report official `resolved` restricted to the discriminator subset
  (gold resolves, base sentinel does not).
- Keep all 20 results visible; do not replace tasks or drop failures silently.
- Never claim "the issue was actually fixed" from a metric-blind case.
- Gold FAIL or an infrastructure error on any case pauses the cohort for
  diagnosis, not exclusion.
