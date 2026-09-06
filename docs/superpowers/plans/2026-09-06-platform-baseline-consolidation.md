# MedChat Platform Baseline Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the complete 537-commit development history while producing one clean, fully verified `codex/platform-v1-baseline` snapshot branch that can enter `main` through a draft Pull Request.

**Architecture:** Freeze the source branch behind local archive refs, leave the dirty original worktree untouched, and build the baseline in a new worktree from the two local-main documentation commits. Use a squash merge without committing to materialize only the final tree, remove credential-scanner false positives before the first baseline commit, then run all release gates and create stacked draft PRs without merging them.

**Tech Stack:** Git worktrees and refs, PowerShell 7, Python/pytest, Node.js static tests, existing MedChat acceptance runner, GitHub CLI when authenticated.

---

## File Map

**Create on the source archive branch:**

- `docs/superpowers/plans/2026-09-06-platform-baseline-consolidation.md` — exact execution and rollback procedure.

**Create as Git references:**

- `codex/archive/platform-stack-2026-09-06` — immutable pointer to the complete source stack.
- `archive/platform-stack-2026-09-06` — annotated local tag for the complete source stack.
- `codex/main-local-docs-2026-09-06` — PR branch preserving the two local-main documentation commits.
- `codex/platform-v1-baseline` — clean final-tree baseline branch.

**Create as a worktree:**

- `D:/MedChat/molecular_chat_system_worktrees/platform-v1-baseline` — isolated baseline assembly and verification directory.

**Modify only inside the new baseline worktree before its first commit:**

- `tests/agent/test_chat_handler_agent_events.py` — construct fake OpenAI-compatible keys at runtime so the tracked source does not resemble a credential.
- `tests/agent/test_semantic_input_gates.py` — same scanner-safe test fixture construction.
- `tests/test_llm_molecular_generator.py` — same scanner-safe test fixture construction.

**Create inside the new baseline worktree after verification:**

- `docs/handoff/platform-v1-baseline-pr.md` — source SHAs, exact validation outcomes, known partial results, rollback procedure and PR review focus.

No production behavior is changed by the fixture rewrite. All other baseline files come from the final tree of `codex/target-search-authoritative-fallback`.

## Task 1: Commit This Plan and Establish Immutable Source Facts

**Files:**

- Create: `docs/superpowers/plans/2026-09-06-platform-baseline-consolidation.md`

- [ ] **Step 1: Verify the source branch and excluded untracked file**

Run in `D:/MedChat/molecular_chat_system_worktrees/opensandbox-stability-hardening`:

```powershell
git branch --show-current
git status --short
git rev-parse HEAD
git rev-parse main
git rev-parse origin/main
```

Expected:

- current branch is `codex/target-search-authoritative-fallback`;
- the only pre-existing untracked item is `data/molecular_faiss_index.index.manifest.json` plus this plan before commit;
- `main` and `origin/main` are not mutated.

- [ ] **Step 2: Commit only the plan**

```powershell
git add -- docs/superpowers/plans/2026-09-06-platform-baseline-consolidation.md
git diff --cached --check
git commit -m "docs: plan platform baseline consolidation"
```

Expected: exactly one documentation file is committed and the manifest remains untracked.

- [ ] **Step 3: Capture stable SHAs and original-worktree status digest**

```powershell
$source = git rev-parse HEAD
$localMain = git rev-parse main
$remoteMain = git rev-parse origin/main
$dirtyStatus = git -C D:/MedChat/molecular_chat_system status --porcelain=v1 -uall
$statusDigest = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData(
    [Text.Encoding]::UTF8.GetBytes(($dirtyStatus -join "`n"))
  )
).ToLowerInvariant()
[pscustomobject]@{
  source = $source
  local_main = $localMain
  origin_main = $remoteMain
  original_worktree_status_sha256 = $statusDigest
}
```

Expected: four non-empty values. Record the digest in the execution report; do not store file contents or credentials.

## Task 2: Create Local Archive and PR Base References

**Files:** none; Git refs only.

- [ ] **Step 1: Prove the target refs do not already exist**

```powershell
$refs = @(
  'refs/heads/codex/archive/platform-stack-2026-09-06',
  'refs/tags/archive/platform-stack-2026-09-06',
  'refs/heads/codex/main-local-docs-2026-09-06',
  'refs/heads/codex/platform-v1-baseline'
)
foreach ($ref in $refs) {
  git show-ref --verify --quiet $ref
  if ($LASTEXITCODE -eq 0) { throw "Ref already exists: $ref" }
}
```

Expected: no ref exists. If one exists, stop and inspect it rather than moving or deleting it.

- [ ] **Step 2: Create the archive branch and annotated tag**

```powershell
git branch codex/archive/platform-stack-2026-09-06 HEAD
git tag -a archive/platform-stack-2026-09-06 HEAD -m "Archive MedChat platform development stack before V1 baseline consolidation"
```

Expected: both refs resolve to the exact source SHA captured in Task 1.

- [ ] **Step 3: Preserve the local-main documentation commits as a PR branch**

```powershell
git branch codex/main-local-docs-2026-09-06 main
git rev-list --left-right --count origin/main...codex/main-local-docs-2026-09-06
```

Expected: `0 2` — no remote-main commit is missing and exactly two local documentation commits are present.

- [ ] **Step 4: Verify no existing branch moved**

```powershell
git rev-parse codex/target-search-authoritative-fallback
git rev-parse codex/archive/platform-stack-2026-09-06
git rev-parse main
git rev-parse origin/main
```

Expected: the first two SHAs equal the Task 1 source SHA; main SHAs remain unchanged.

## Task 3: Materialize the Final Tree in a Clean Worktree

**Files:** all tracked files represented by the final-tree delta; no user-untracked files.

- [ ] **Step 1: Verify the destination directory is absent**

```powershell
$path = 'D:/MedChat/molecular_chat_system_worktrees/platform-v1-baseline'
if (Test-Path -LiteralPath $path) {
  throw "Baseline worktree path already exists: $path"
}
```

Expected: no exception.

- [ ] **Step 2: Create the baseline branch from the local-main PR base**

```powershell
git worktree add -b codex/platform-v1-baseline `
  D:/MedChat/molecular_chat_system_worktrees/platform-v1-baseline `
  codex/main-local-docs-2026-09-06
```

Expected: the new worktree is clean and starts at the local-main SHA.

- [ ] **Step 3: Materialize the source tree without creating a merge commit**

Run in the new worktree:

```powershell
git merge --squash --no-commit codex/archive/platform-stack-2026-09-06
```

Expected: the final source-tree delta is staged and no commit exists yet. If conflicts occur, stop and report the exact paths; do not auto-resolve semantic conflicts.

- [ ] **Step 4: Enforce excluded-path boundaries before any commit**

```powershell
$changed = @(git diff --cached --name-only)
$forbidden = $changed | Where-Object {
  ($_ -match '(^|/)\.env($|\.)' -and $_ -notmatch '(^|/)\.env\.example$') -or
  $_ -match '(^|/)(outputs/|scratch/|logs/|__pycache__/)' -or
  $_ -match '\.(sqlite|sqlite3|db|pt|pth|ckpt|index)$' -or
  $_ -eq 'data/molecular_faiss_index.index.manifest.json'
}
if ($forbidden) {
  $forbidden
  throw 'Forbidden runtime or sensitive paths are staged'
}
```

Expected: no forbidden path is printed.

- [ ] **Step 5: Verify local-main documentation survived the snapshot**

```powershell
git diff --cached --name-status -- `
  docs/superpowers/specs/2026-06-18-agent-platform-enhancement-design.md `
  docs/superpowers/plans/2026-06-18-agent-platform-enhancement.md
```

Expected: neither file is deleted.

## Task 4: Make Credential Test Fixtures Scanner-Safe Before the First Baseline Commit

**Files:**

- Modify: `tests/agent/test_chat_handler_agent_events.py`
- Modify: `tests/agent/test_semantic_input_gates.py`
- Modify: `tests/test_llm_molecular_generator.py`

- [ ] **Step 1: Run the exact CI credential scan and verify RED**

```powershell
git grep --cached -IlE 'sk-[A-Za-z0-9]{20,}|BEGIN (RSA|OPENSSH) PRIVATE KEY' `
  -- . ':(exclude,glob)**/*.md'
```

Expected: the three listed test files are reported. No credential value is printed.

- [ ] **Step 2: Rewrite only fake fixtures as scanner-safe runtime expressions**

For each fake key literal, replace a contiguous tracked literal such as:

```python
fake_key = "sk-testplaceholder0123456789"
```

with runtime-equivalent construction:

```python
fake_key = "sk" + "-" + "testplaceholder0123456789"
```

When the literal appears directly in a mapping or environment call, introduce a local `fake_key` variable in that test. Assertions must still exercise a value beginning with `sk-`; do not weaken redaction or validation behavior.

- [ ] **Step 3: Stage the fixture rewrites and verify GREEN**

```powershell
git add -- `
  tests/agent/test_chat_handler_agent_events.py `
  tests/agent/test_semantic_input_gates.py `
  tests/test_llm_molecular_generator.py

$matches = git grep --cached -IlE `
  'sk-[A-Za-z0-9]{20,}|BEGIN (RSA|OPENSSH) PRIVATE KEY' `
  -- . ':(exclude,glob)**/*.md'
if ($LASTEXITCODE -eq 0) {
  $matches
  throw 'Credential-like material remains in the staged baseline'
}
if ($LASTEXITCODE -ne 1) { throw 'Credential scan failed unexpectedly' }
```

Expected: no paths are reported and exit code semantics indicate no match.

- [ ] **Step 4: Run focused behavior tests**

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -m pytest `
  tests/agent/test_chat_handler_agent_events.py `
  tests/agent/test_semantic_input_gates.py `
  tests/test_llm_molecular_generator.py `
  -q -p no:cacheprovider
```

Expected: all focused tests pass and fake-key validation/redaction behavior remains covered.

## Task 5: Commit the Clean Platform Snapshot

**Files:** all staged baseline files.

- [ ] **Step 1: Inspect the staged boundary and size distribution**

```powershell
git diff --cached --check
git diff --cached --stat
git status --short
```

Expected: only tracked platform source, tests, documentation and deployment assets are staged; no unexpected untracked file appears in the clean worktree.

- [ ] **Step 2: Reject staged blobs larger than 10 MiB**

```powershell
$large = @()
foreach ($path in @(git diff --cached --name-only --diff-filter=ACMR)) {
  $entry = git ls-files --stage -- $path
  if (-not $entry) { continue }
  $blob = ($entry -split '\s+')[1]
  $size = [int64](git cat-file -s $blob)
  if ($size -gt 10MB) {
    $large += [pscustomobject]@{ path = $path; bytes = $size }
  }
}
if ($large) {
  $large | Format-Table -AutoSize
  throw 'Baseline contains staged blobs larger than 10 MiB'
}
```

Expected: no oversized staged blob.

- [ ] **Step 3: Commit one explicit baseline snapshot**

```powershell
git commit -m "chore: establish MedChat platform v1 baseline"
```

Expected: one commit contains the final-tree delta and scanner-safe fixtures; the original 537-commit branch remains unchanged behind archive refs.

- [ ] **Step 4: Verify baseline ancestry and compactness**

```powershell
git rev-list --left-right --count codex/main-local-docs-2026-09-06...HEAD
git log --oneline --decorate -3
```

Expected: `0 1` and exactly one platform snapshot commit above the local-main PR branch.

## Task 6: Run Release Gates on the Baseline

**Files:** test outputs are ignored and must not be committed.

- [ ] **Step 1: Run Python compilation and focused Agent tests**

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -m compileall -q src scripts
C:/Users/xkx52/.conda/envs/MedChat/python.exe -m pytest tests/agent -q -p no:cacheprovider
```

Expected: compilation succeeds; Agent tests pass with only documented opt-in skips.

- [ ] **Step 2: Run the full deterministic Python suite**

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -m pytest tests -q -p no:cacheprovider
```

Expected: all deterministic tests pass. Any failure blocks PR creation and is reported without modifying `main`.

- [ ] **Step 3: Run every repository Node test**

```powershell
$failed = @()
$nodeTests = @(git ls-files 'tests/*.js' 'tests/**/*.js' | Sort-Object -Unique)
foreach ($test in $nodeTests) {
  node $test
  if ($LASTEXITCODE -ne 0) { $failed += $test }
}
if ($failed) {
  $failed
  throw 'One or more Node tests failed'
}
```

Expected: every tracked top-level Node test exits zero.

- [ ] **Step 4: Run contract acceptance**

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe `
  scripts/run_agent_acceptance.py --mode contract `
  --output scratch/platform-v1-contract.json
```

Expected: overall status `passed`, 34/34 contract cases passed.

- [ ] **Step 5: Run golden real acceptance once**

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe `
  scripts/run_agent_acceptance.py --mode real --case-set golden --repeat 1 `
  --output scratch/platform-v1-golden-real.json
```

Expected: real tool outcomes are recorded honestly. Missing RG-MPNN weights or reverse-target data may remain `partial`; fabricated scientific values are a hard failure.

- [ ] **Step 6: Re-run repository safety gates**

```powershell
git diff --check codex/main-local-docs-2026-09-06..HEAD

git grep -IlE 'sk-[A-Za-z0-9]{20,}|BEGIN (RSA|OPENSSH) PRIVATE KEY' `
  -- . ':(exclude,glob)**/*.md'
if ($LASTEXITCODE -eq 0) { throw 'Credential-like material exists in baseline HEAD' }
if ($LASTEXITCODE -ne 1) { throw 'Credential scan failed unexpectedly' }

git status --short
```

Expected: no diff error, no credential-like tracked material, and no staged or tracked test artifacts.

- [ ] **Step 7: Prove the original dirty worktree is unchanged**

```powershell
$dirtyStatus = git -C D:/MedChat/molecular_chat_system status --porcelain=v1 -uall
$afterDigest = [Convert]::ToHexString(
  [Security.Cryptography.SHA256]::HashData(
    [Text.Encoding]::UTF8.GetBytes(($dirtyStatus -join "`n"))
  )
).ToLowerInvariant()
$afterDigest
```

Expected: the digest exactly equals the value captured in Task 1.

## Task 7: Record Verification, Push Stacked Branches and Create Draft PRs

**Files:** none; remote Git references and PR metadata only.

- [ ] **Step 1: Create and commit the PR verification record**

Use `apply_patch` to create `docs/handoff/platform-v1-baseline-pr.md`. Record only values observed in Task 6 under these fixed headings:

```markdown
# MedChat Platform V1 Baseline PR

## Scope

This draft PR consolidates the final MedChat platform tree while the complete original development history remains reachable from the local archive branch and tag.

## Source identity

- Source branch and commit
- Baseline parent commit
- Baseline snapshot commit
- Local archive branch and tag

## Included systems

- Agent scientific contracts, orchestration, persistence and evaluation
- LangGraph canary harness
- Temporal durable docking runtime and deployment assets
- OpenSandbox docking broker and stability hardening
- Web model configuration, safe rendering and workflow completion fixes
- Authoritative target lookup, molecular generation binding and candidate ranking

## Verification

- Python compilation
- Focused Agent tests
- Full deterministic Python suite
- Node test suite
- Contract acceptance
- Golden real acceptance
- Credential scan
- Oversized-blob scan
- Original-worktree status digest comparison

## Honest partial or skipped outcomes

List each dependency-related partial or skip exactly as reported. Do not rewrite it as passed.

## Review focus

- Scientific provenance and anti-hallucination gates
- Durable task and sandbox trust boundaries
- Deployment defaults and migration risk
- Frontend structured result rendering
- Target-source cache lifetime and remote evidence semantics

## Rollback

Do not merge this draft automatically. If the baseline is later merged and must be rolled back, create a normal revert PR; never force-reset main.
```

Replace each bullet under “Source identity” and “Verification” with the exact command result rather than leaving generic labels. Then run:

```powershell
git add -- docs/handoff/platform-v1-baseline-pr.md
git diff --cached --check
git commit -m "docs: record platform v1 baseline verification"
```

Expected: one documentation-only commit above the baseline snapshot.

- [ ] **Step 2: Verify GitHub authentication without printing credentials**

```powershell
gh auth status
```

Expected: authenticated to the repository host. Authentication failure blocks only remote publication, not the local baseline.

- [ ] **Step 3: Push the two PR branches only**

```powershell
git push -u origin codex/main-local-docs-2026-09-06
git push -u origin codex/platform-v1-baseline
```

Do not push the archive branch or archive tag until its historical credential-like fixtures have received a separate history audit.

- [ ] **Step 4: Create the local-main documentation draft PR**

```powershell
gh pr create --draft `
  --base main `
  --head codex/main-local-docs-2026-09-06 `
  --title "docs: preserve local agent platform plans" `
  --body "Preserves the two local documentation commits that were ahead of origin/main. No business code changes. Review the platform enhancement design and implementation plans before merging."
```

Expected: one draft PR URL.

- [ ] **Step 5: Create the stacked platform baseline draft PR**

```powershell
gh pr create --draft `
  --base codex/main-local-docs-2026-09-06 `
  --head codex/platform-v1-baseline `
  --title "chore: establish MedChat platform v1 baseline" `
  --body-file docs/handoff/platform-v1-baseline-pr.md
```

Expected: a second draft PR targeting the documentation branch. It must not be merged automatically.

- [ ] **Step 6: Record final state**

```powershell
git status --short
git log --oneline --decorate -5
git worktree list
gh pr status
```

Expected: baseline worktree is clean, both PRs are draft, archive refs remain local, and `main` has not moved.

## Completion Criteria

- The complete source history is reachable from a local archive branch and annotated tag.
- The original dirty worktree has the same status digest before and after migration.
- `codex/platform-v1-baseline` is one compact final-tree snapshot above the two local-main documentation commits.
- No real or credential-like API key is tracked in baseline HEAD.
- No `.env`, runtime database, model weight, output, scratch report or FAISS manifest is committed.
- Deterministic tests, compilation, Node tests and contract acceptance pass.
- Golden real acceptance reports honest passed/partial/failed outcomes.
- Two draft PRs exist in dependency order, and neither is merged.
- Archive history is not pushed until a separate historical-secret audit approves it.
