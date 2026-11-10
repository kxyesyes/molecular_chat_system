# Candidate list input and comparison plan

Goal: preserve a user's multiline SMILES list and execute supported requested property/ADMET comparisons without inventing a composite score.

Architecture: retain FastAPI/Jinja/native JS. Replace the single-line composer with a textarea and retain Enter-send/Shift+Enter-newline. Keep existing scientific validation and distinguish a grammatical molecular subject from metric names; unknown endpoints and malformed explicit structures must still be rejected.

Observed failures: actual UI stripped newlines between two valid explicit SMILES; the separated request then rejected the grammatical subject “以下两个分子” as an unknown metric. RDKit accepts both full structures. The endpoint comparison and negative score clause also require inspection before claiming support.

- [x] Add failing composer markup/keyboard contract check, then migrate the input and its existing selectors/styles. Do not add a framework or a generic editor.
- [x] Add failing admission tests for the original two-structure requests and inspect endpoint/presentation ownership. Fix identified boundaries only; retain unknown-metric, exclusion and whole-structure checks.
- [x] Run focused Python regressions, compileall, node --check and affected Node contracts sequentially.
- [x] Use the guarded single server and configured frontend model for the original multiline comparison, then evidence-grounded followups. Verify Shift+Enter and Enter behavior on the real page, save screenshot, and stop the recorded server.
