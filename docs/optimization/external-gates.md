# External verification gates

Repository implementation is complete without claiming unverified providers as live.

## Xianyu

- Current baseline: `BLOCKED_EXTERNAL` by `AUTH_EXPIRED`; one request, zero pages, no retry.
- Required before `VERIFIED_LIVE`: user-driven login/reload, one explicitly authorized low-frequency
  search, correct exhausted/partial behavior, and no challenge/rate-limit bypass.

## Cloud text model

- Current baseline: `NOT_VERIFIED_LIVE`; rules-only analysis is the default.
- Required before enabling by default: configured OpenAI-compatible endpoint, credential held only
  in local environment, valid JSON/evidence behavior, timeout/cost measurements, and a labeled
  multi-category comparison showing benefit over the rules baseline.

## Retail adapters

- Automatic JD/Taobao/Tmall/PDD adapters: `NOT_CONFIGURED`.
- Quote Import: `IMPLEMENTED` and fixture-tested; Mock/Dry-run is rejected.
- The dated commit/license/dependency/runtime audit for all three planned candidates is in
  `retail-adapter-audit.md`; none passed its live-price gate.
- Before enabling an automatic adapter, record upstream commit, actual LICENSE file and dependency
  obligations, credentials, macOS ARM behavior, real purchasable price semantics, SKU/bundle match,
  challenge handling, and a non-Mock query. Passing browser startup or login alone is insufficient.

These gates require user credentials, external service state, or explicit live-access authorization.
They are acceptance dependencies rather than missing local code.
