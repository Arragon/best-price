# Search strategy

Build a small alias set with a reason for each term. Count actual platform attempts against a
declared budget, including failed attempts. Broad discovery queries identify models and leads;
focused queries establish same-model, same-bundle evidence.

Use `pace=economy|balanced|fast` as an execution preference. Favor cached runs and local
analysis when speed matters. `force_refresh` skips result cache only; it does not bypass pacing
or restrictions. Stop when coverage is adequate, two useful searches add no candidates, budget
is exhausted, or the platform requires human action.

Deduplicate by product identity across keywords before counting samples. Do not treat a one-page
cached run as two-page coverage. Preserve the original `observed_at` when reusing data.
