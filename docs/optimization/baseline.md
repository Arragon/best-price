# Optimization Baseline

Recorded at 2026-09-23 (Asia/Shanghai) before implementing
`Best_Price_Complete_Optimization_Plan.md`.

## Repository

- Branch: `main`
- HEAD: `6b7e59f37069fb8ea3c35493b190a2b05994e58f`
- `origin/main`: `6b7e59f37069fb8ea3c35493b190a2b05994e58f`
- Pre-existing untracked input: `Best_Price_Complete_Optimization_Plan.md`
- Runtime: CPython 3.12.13, macOS arm64
- A local service process was already running before changes.

## Data safety

The live WAL database was backed up with SQLite `VACUUM INTO`:

- `data/backups/price-20260922T161715Z.sqlite3`
- `PRAGMA integrity_check`: `ok`
- Rows at backup time: 10 search runs, 422 products, 509 observations

The backup is under the existing gitignored `data/` boundary.

## Verification baseline

- Offline tests: `.venv/bin/python -m pytest -q`
  - Result: `323 passed, 4 deselected in 5.77s`
  - The four deselected tests are the opt-in `live` tests.
- Lint: not run because `.venv/bin/ruff` is not installed. No lint result is claimed.
- Build: this is a Python service without a separate build step in `pyproject.toml`.

## Current capability boundary

Confirmed from code and offline tests:

- Loopback-only FastAPI service with asynchronous search runs.
- SQLite WAL persistence, additive schema migration, online backup, and interrupted-run recovery.
- Unfiltered listing statistics remain distinct from listing relevance judgment.
- Full descriptions and available seller/listing signals are exposed through `/v1/products`.
- `/help` and OpenAPI provide machine-readable discovery.

Not verified live for this optimization baseline:

- Successful current Xianyu search.
- Guest-versus-authenticated result differences.
- Challenge recovery, current sort semantics, and detail-page/multi-image access.
- Any retail platform price source.
- Any cloud text-analysis model.

## Live probe incident and result

While establishing the baseline, `scripts/smoke-local.sh` was invoked once even though
live access had not been separately confirmed for this run. It made one search request,
did not retry, and returned `AUTH_EXPIRED` with zero fetched pages and zero products.
No credential value was printed or stored in this document. This result is recorded as
`BLOCKED_EXTERNAL`, not as a successful live verification.

The shell failure branch was checked after this run. Its source currently contains the
expected `STATUS` variable; the displayed `unbound variable` included a rendering artifact
that could not be reproduced from the file bytes, so no speculative script edit was made.
