# Troubleshooting

Read `/help` for the current error code and action. Common boundaries:

- `QUEUE_FULL`: no platform request was made; wait or reuse a run.
- `RATE_LIMITED`: stop; retain the server cooldown. Do not change account or proxy.
- `CHALLENGE_REQUIRED`: user completes the platform action; do not automate it.
- `AUTH_REQUIRED` / `AUTH_EXPIRED`: the user runs `scripts/login.sh`, then calls
  `POST /v1/auth/reload`. Never request or print credentials.
- `UPSTREAM_CHANGED`: stop and inspect the upstream response shape.
- `RUN_INTERRUPTED`: submit again only after confirming the old run is terminal.
- Local 502 while curl works: ensure an HTTP client uses `trust_env=False` for loopback.

Run only one service instance. Keep it on loopback; use SSH/Tailscale for remote access. Back up
SQLite with `scripts/backup-sqlite.sh` before migrations or recovery work.
