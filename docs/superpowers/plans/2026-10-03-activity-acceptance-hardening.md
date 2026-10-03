# Activity Acceptance Hardening

## Goal

Close remaining acceptance gaps that could mark empty molecular-generation or
activity-prediction observations as scientifically successful. Preserve the
existing family-model contract, provenance, failure states, and public APIs.

## Scope

1. Add regression tests for empty generated-candidate acceptance and empty
   successful activity observations.
2. Make the scientific acceptance check require at least one valid, unique
   generated candidate and reject zero-output success.
3. Make the activity result validator reject an empty successful observation
   even when it is passed directly as a `ToolResult`.
4. Run focused activity/acceptance tests, then the broader activity test set
   and compile checks.

## Non-goals

- No model training, external API calls, or real credentials.
- No router/planner rewrite.
- No changes to the existing pIC50 threshold, family selection, or model
  registry semantics.
- No changes to the root worktree or unrelated untracked files.

## Verification

- Add tests first and observe the failing state.
- Run the focused tests after the minimal implementation.
- Run the activity contract, family, acceptance, and compile checks before
  committing.
