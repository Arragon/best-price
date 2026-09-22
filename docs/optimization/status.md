# Optimization completion status

Recorded on 2026-09-23 after implementing the repository-local work in
`Best_Price_Complete_Optimization_Plan.md`. This file separates implementation evidence from
volatile external-provider verification.

## Phase results

| Phase | Status | Repository evidence |
|---|---|---|
| 0 — baseline and safety | `IMPLEMENTED` | Baseline, Git state, backup, row counts, test baseline, and live boundaries are in `baseline.md`. |
| 1 — search scheduling and Skill | `IMPLEMENTED`, `TESTED_WITH_FIXTURE` | Exhaustion state, semantic fingerprint, in-flight merge, cache, bounded queue, persisted restrictions, task pace, resumable/full JSON CLI, and project Skill. |
| 2 — buying research | `IMPLEMENTED`, `TESTED_WITH_FIXTURE` | Three research modes, sourced SearchProfile constraints, request budget, run linkage, dynamic candidates, separate model evaluations, buckets, and stopping reason. |
| 3 — text analysis | `IMPLEMENTED`, `TESTED_WITH_FIXTURE` | Conservative rules baseline, optional OpenAI-compatible provider, evidence validation, hash cache, graceful failure, and 100 labeled cross-category cases. Cloud provider remains `NOT_VERIFIED_LIVE`. |
| 4 — listing evaluation | `IMPLEMENTED`, `TESTED_WITH_FIXTURE` | Deterministic five-part score, nullable score states, evidence/risk/feedback history, deduplicated same-SKU comparable asking-price statistics, and model/listing separation. |
| 5 — new-price comparison | `IMPLEMENTED`, `TESTED_WITH_FIXTURE`, `BLOCKED_EXTERNAL` for automatic adapters | Audited quote import, mock rejection, HTTPS/host/canonical URL validation, explicit SKU match, freshness, full-cost savings, and configurable new-price preference signal. No automatic provider has passed its live gate. |
| 6 — productization | `IMPLEMENTED`, `TESTED_WITH_FIXTURE` | Ranked model/listing output, capabilities, OpenAPI/help/README/Skill alignment, bounded rotating logs, feedback, external gates, and local resource measurement. |

## Acceptance matrix

- Existing API and CLI defaults remain compatible; raw `/v1/stats` is still explicitly unfiltered.
- The v2 backup migrated to v4 twice with `PRAGMA integrity_check=ok`; 10 runs, 422 products, and
  509 observations were unchanged.
- Pagination exhaustion, partial/error states, deduplication, incompatible fingerprints, restart
  safety state, bounded queue, cache/refresh, and pace floors have offline regression coverage.
- The project Skill is discovered by this Codex workspace as `best-price`; OpenCode discovery is
  `BLOCKED_EXTERNAL` because that client is not available in this execution environment.
- SearchProfile persists target versus hard budget, sourced required/preferred constraints,
  Primary/Extra/Review/Excluded candidates, request budget, and optional `<=` savings preference.
- Text labels and evaluation risks require source evidence. Invalid model output and unavailable AI
  degrade transparently without deleting raw listings or manufacturing a score.
- Same-SKU comparable statistics use each product's latest research-linked observation once and
  expose source runs/items. The original raw statistics are not overwritten.
- Mock/Dry-run quotes and mismatched exact SKUs are rejected. Savings stay unknown until both sides'
  mandatory costs are known; conditional or unverified quotes cannot produce a definitive saving.
- All three proposed retail reuse candidates have a dated commit/license/dependency/runtime audit;
  no unlicensed or unverified implementation was copied into this repository.
- API remains loopback-only by default, secrets stay in local environment configuration, and log
  files rotate at a bounded size/count.

## Verification evidence

- Offline suite: `.venv/bin/python -m pytest -q` (final result is recorded in README and the
  implementation report).
- Skill validation: system `quick_validate.py` for both the primary Skill and compatibility pointer.
- Syntax/import check: `.venv/bin/python -m compileall -q src tests`.
- Scoped Ruff check for all new Phase 2–6 modules and their new/edited acceptance tests: passed.
  The whole repository still reports historical style findings outside this optimization scope, so
  no full-repository lint pass is claimed.
- Migration rehearsal: existing backup v2 -> v4 -> v4, all 13 new derived tables present,
  integrity `ok`, original counts unchanged.
- Local synthetic resource probe on Mac17,5 arm64: 1,000 loopback TestClient requests across
  `/health` and `/v1/capabilities`, 0.326 s, 3,071.5 requests/s, 54.5 MiB process peak RSS. This is
  a lightweight API microbenchmark, not a Xianyu throughput or long-running production claim.

## External gates

The only remaining acceptance items require user credentials, another installed client, explicit
live-access authorization, or external service state. They are listed in `external-gates.md` and
must not be represented as locally completed or current-provider success.
