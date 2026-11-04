# Historical long-horizon oracle replay

The preserved Eval 5 pair was replayed after verifying its frozen manifests.
Oracle code ran only in the existing rootless, network-isolated sandbox against
the sandbox's copied workspace. `/home/iclab/bid-eval/` was read-only and was
not modified.

## Manifest checks

- `evidence/longhorizon/SHA256SUMS`: **2/2** payload files match.
- `evidence/longhorizon/runs/SHA256SUMS`: **180/180** payload entries match.
  The remaining entry is the manifest's own path recorded with the empty-file
  SHA-256 sentinel; the nonempty manifest itself cannot self-hash to that
  value. No payload mismatch or missing payload was found.
- The archived `runs/summary.json` reports BID `oracle_passes=5`,
  `oracle_fails=6`; Direct `oracle_passes=0`, `oracle_fails=1`.

## Replay

| Workspace | Sandbox result | Reproduced summary |
|---|---|---|
| `runs/bid-ws` | exit 1, five module-import PASS groups and six failure groups | 5 pass / 6 fail |
| `runs/direct-ws` | exit 1, first module missing | 0 pass / 1 fail |

The evaluator source SHA-256 is
`7721f816d395c89f54dd28471c58b60f87fccd82943665482c8b76201d81d564`.
Replay outputs and the exact source summary/manifests/evaluator are preserved
under `historical-replay/`. Replay JSON hashes: BID
`50c915fcb8d0ed12aa5222a14c0bb1bed297dd54b0584e779033e9864656c6c9`, Direct
`0a373deec94071038d7231ffb37182ce9f5eed1f49018fdcca5038dbdaafdc8d`.

## Still open

This verifies and reproduces the named Eval 5 pair, not every historical
evaluation. `benchmark-v2/task.txt` matches its manifest hash
`971dc0b9c4fd99af543196d668403858bf361612e90627d960e232c63f5564b8`, but the
adjacent hidden-oracle sidecar expects
`bc704fb6e798e2ee86caf6fa8a5a99e1cf71016dbcdf219289dcec2c3c1d6988`, while the
oracle file hashes to
`6b610508fa9c6cf1cbec7c9f95ad98e672a6bf3957f2dc0030d4494893c35acc`. Both
were preserved. No corresponding final benchmark-v2 workspaces were found in
that directory, so no replay was attempted for that contract.
