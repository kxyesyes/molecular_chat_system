# Target lookup intent boundary implementation plan

**Goal:** Retrieve requested protein structures without executing unrequested generation or docking.

**Architecture:** Make generation intent distinguish the grammatical output head and lookup context for the affected occurrence; recognize small-molecule docking as a suitability qualifier only where it modifies a structure lookup. Keep independently explicit generation and docking obligations visible to the existing planner and its input gates.

**Tech Stack:** Python generation contract, hybrid router, Pi Web requirements, pytest, actual running Web UI.

- [x] Add RED-first direct generation, routing, and Web planning regressions for the observed query and independent-action counterexamples.
- [x] Fix only the relevant intent boundaries; preserve count, negation and scientific source validation.
- [x] Run final parser/router/Web regressions and compileall; review for skipped extra requests.
- [x] Restart the test instance and verify the actual original request and an independent explicit action from the configured Agent model.

## Validation and limits

The final-source real Web request for human EGFR performed only target search;
the result lists actual source records and now explains that ligand roles and
pocket suitability remain unverified. A separate request to dock CCO was
rejected pending a more precise request, with no scientific tool dispatched.
A distinct explicit generation request executed the generation tool and
returned one real candidate. The Agent remained the frontend-configured
`deepseek-v4-pro`; generation used `gmm-llama:latest`.

The data path selected by the user was saved to the Windows user environment,
without copying data. `scripts/health_check.py --strict` using that saved value
passed 24/24 checks, with two skipped invalid activity-registry record warnings.
The 6025 UI test instance explicitly used the smaller local reverse-target source
to avoid a second simultaneous full-source preload. The full 831,120-row source
was previously verified in the separate running instance. Newly spawned shells
must inherit the updated user environment; an already-running host may require
restart or an explicit refresh. The configured asset is in another worktree and
will be unavailable if that directory is removed.

Independent review found and prompted regression tests for malformed counts,
explicit docking actions, use-versus-action questions and structure modifiers.
Natural-language recognition remains bounded; tested ambiguous extra tasks fail closed.
The final 15-file regression passed 898 tests with seven pre-existing deprecation
warnings. `compileall` and `git diff --check` passed; the latter reported an
existing unrelated CRLF warning in `controller.py`.
