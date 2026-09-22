# Architecture

Best Price is a loopback-only, single-worker buying-data service. External agents own search
planning and final recommendations; the service owns collection, provenance, persistence,
pacing, arithmetic, and reproducible derived results.

## Boundaries

- `adapters/`: platform access and extraction of platform facts. Upstream Xianyu code remains an
  independent gitignored checkout because its license is unresolved.
- `services/search_service.py`: durable run lifecycle, request deduplication, cache reuse, and
  normalization/persistence orchestration.
- `services/search_scheduler.py`: advisory task pace below persistent platform safety state.
  It is single-worker and stores cross-restart attempt/restriction state in SQLite.
- `storage/`: additive migrations only. `products` and `observations` contain platform facts and
  deterministic normalization; future AI/evaluation/user-preference data belongs in derived tables.
- `api/`: strict Pydantic contracts. Existing raw `/v1/stats` remains unfiltered permanently;
  future comparable statistics must use a separate endpoint.
- `ai/` and `evaluation/`: optional evidence extraction plus deterministic score rules. Model
  failure falls back to explicit rules and never removes original observations.
- `storage/research_repository.py`, `evaluation_repository.py`, and `retail_repository.py` own
  derived domains rather than expanding the raw Repository into a god object.
- `.agents/skills/best-price/`: external-agent workflow and reporting discipline. It discovers
  implemented capabilities from `/help` and OpenAPI instead of assuming planned endpoints exist.
- Process logging is console plus a bounded `RotatingFileHandler`; uvicorn shares that logging
  configuration so a long-running local service cannot grow one unbounded log file.

## Search request flow

`POST /v1/search` obtains a local auth snapshot, calculates a semantic fingerprint, reuses an
active or fresh successful run when allowed, enforces queue capacity, then creates a durable run.
The scheduler checks persisted cooldown/human-action state, waits for the effective pace floor,
records the attempt before network I/O, and invokes exactly one adapter at a time. Page outcomes
distinguish successful fetch, normal exhaustion, and error. Results are normalized and appended to
SQLite; raw unfiltered statistics are computed from that run only.

## Compatibility invariants

- Existing endpoint meanings and default CLI text remain stable.
- `force_refresh` never bypasses scheduler restrictions.
- Normal exhaustion is success; only error outcomes produce partial/failed runs.
- Cached runs retain their original observation time and run identity.
- SQLite migrations are additive and idempotent; backup precedes live migration.
- Research, analysis, evaluation, and quotes are derived records with provenance and versions;
  they cannot overwrite platform facts.
- New-versus-used preference thresholds are advisory report signals. They never delete or silently
  re-bucket a listing.
