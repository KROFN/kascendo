# Public code release audit

Source checkout: private local retrieval subsystem at
`/srv/node2-agent/workspace/kascendo-retrieval-v01`.

Original source revision (local Git): `3888886` (2026-09-10).
Public export created 2026-11-19 in a new, independent Git directory.
It does not preserve the private repository history.

## Contents

- Real Python `retrieval/` modules and `serve.py`.
- Runnable `unittest` suite with synthetic, non-personal material.
- Public quality report and explanation of the failed v0.4 frozen gate.
- Local evaluation CLI, publishing guardrails and GitHub Actions CI.
- Two synthetic learning units replacing the original Stepik mapping.

## Actual Node2 verification

Environment: Python 3.14.4, libsql==0.1.11, Ruff 0.14.7.

- Unit and offline integration: **36/36 passed**.
- Full HTTP smoke: **passed**, actual localhost server and synthetic embedding
  endpoint, source URL and roundtrip checked.
- Ruff static check: **passed**. Configuration checks E4/E9/F63/F7/F82,
  not a claim that every style or type-checking rule is satisfied.
- Python bytecode compile: **passed**.
- `git diff --cached --check`: **passed**.
- Public preflight allowlist and common secret pattern scan: **passed**.

These are **local results**, not a claim about GitHub Actions status before
its first real cloud workflow run.

## Publication boundary

Excluded on purpose: 122-video corpus, lesson transcripts, raw evaluation
queries/rankings, user data, database files (including the 731MB vector DB),
model weights, real service unit files, tokens, private paths and original
Git history.

The public checkout demonstrates the real implementation and repeatable
synthetic integration tests; it does not include the private materials
necessary to reproduce the full-corpus ranking metrics. The measured
quality outcome of the private v0.4 release gate remains FAIL/DOGFOOD.

## License

No license was silently selected for the user. Owner should choose a
permissive or other license separately before granting reuse rights.
