# Know-how

- A false `hasNextPage` is normal pagination exhaustion, not a failed page. Keep it distinct from
  network/platform errors or it will incorrectly turn a successful run into `partial`.
- Request equivalence includes normalized keyword, filters, page coverage, adapter version,
  auth mode, and relevant schema version. Pace and cache policy affect execution, not the dataset.
- Persist the platform-attempt timestamp before network I/O. Otherwise a crash/restart erases the
  safety gap and can immediately repeat a request.
- `fast` is advisory. Default deployments retain the legacy minimum interval until
  `ALLOW_FASTER_PACE=true` is explicitly configured; even then the platform floor remains hard.
- A user-driven auth reload may clear auth/challenge stops, but must never clear a rate-limit
  cooldown. Credential refresh is not a rate-limit bypass.
- JSON CLI export must paginate the local products API and preserve descriptions. It does not mean
  fetching unlimited new platform pages.
- The project Skill must treat OpenAPI as the implemented-capability boundary. Planned research,
  evaluation, AI, or retail endpoints are unavailable until present there.
- Evidence text must be a contiguous substring of the declared observation field before an
  evaluation can be stored. Fluent model output is not evidence.
- Five-dimensional listing scores are deterministic under `score-v1`; incomplete dimensions or
  score-suspending risk flags produce a null score instead of weight redistribution.
- New-versus-used savings remain null until SKU match and both sides' shipping/mandatory fees are
  known. Unknown cost is not zero.
- Keep raw observations and all records referenced by a research, evaluation, or quote match. The
  current service has no automatic deletion job; archive/compaction requires a future reference-aware
  policy. Images remain remote URLs and are not downloaded by default.
- Runtime logs use size/count rotation. Changing the path or retention is an operator setting and
  must not expose credentials or raw authentication material.
