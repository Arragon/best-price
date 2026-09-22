# Retail adapter candidate audit

Read-only audit performed 2026-09-23. No candidate was copied, installed, executed, or enabled.
Repository README claims are not treated as proof of live purchasable prices.

| Candidate | Audited HEAD | License evidence | Declared runtime/dependencies | Local/live result | Decision |
|---|---|---|---|---|---|
| [h382110229/price-hunter](https://github.com/h382110229/price-hunter) | `c428245b53363b1032140dd81c49e7f9b01afead`, project `0.2.0`, Python `>=3.12` | README says MIT, but the full tree has no `LICENSE`, `COPYING`, or `NOTICE`; GitHub metadata reports no license. License is therefore unconfirmed. | `mcp`, `httpx`, Pydantic, pydantic-settings, python-dotenv; Taobao/JD/PDD affiliate credentials. Missing credentials intentionally return structural Mock/Dry-run data. | Not run: no approved affiliate credentials, and a credential-free result would be Mock rather than price evidence. | Do not vendor or enable. Re-audit license and prove a non-Dry-run same-SKU quote before writing an adapter. |
| [HaonanYu123/JD-Taobao-MCP](https://github.com/HaonanYu123/JD-Taobao-MCP) | `63a4e143ad54d1d7ea9d7fa85615283beaaf3f1d`, project `0.1.0`, Python `>=3.11` | Root MIT `LICENSE` exists; pyproject also declares MIT. Dependency obligations still require review if adopted. | `mcp[cli]`, Playwright, python-dotenv, visible persistent Chromium and manual login. README installation examples are Windows-focused. | Not installed or run: macOS ARM browser install, resource use, login and real result semantics require user-visible operation and authorization. | Eligible only for a future isolated, on-demand, read-only worker after the macOS/live gate. |
| [lmt-ux/Xianyu-Product-Automated-Analysis-Assistant](https://github.com/lmt-ux/Xianyu-Product-Automated-Analysis-Assistant) | `78d51a302ed70023b39bcee1c9f9404bb207b446`, Python README says `>=3.8` | No license-like file and GitHub metadata reports no license. | FastAPI/Uvicorn, Playwright, Tortoise, aiomysql/MySQL, pandas, openpyxl, OpenAI; browser profiles and local auth files. | Not installed or run. Its stack and data model are materially broader than a price-source adapter, and current-price semantics are not independently verified. | Reference only; no code reuse or redistribution while license is absent. |

## Gate required before any automatic adapter is enabled

1. Verify the exact upstream commit and an actual compatible license file, including dependency
   obligations.
2. Install in an isolated environment on this Mac, never against a daily browser profile.
3. Keep all credentials local and confirm that missing credentials cannot produce an accepted quote.
4. Run one explicitly authorized, user-visible, low-frequency query for a known SKU and prove the
   returned price, bundle, conditions, availability, timestamp, and source URL are real.
5. Confirm challenge/login failures stop for human action and that the process/browser shuts down
   after the on-demand request.
6. Only then add a thin adapter and expose it through `/v1/capabilities`; fixture shape or successful
   browser startup alone is not sufficient.
