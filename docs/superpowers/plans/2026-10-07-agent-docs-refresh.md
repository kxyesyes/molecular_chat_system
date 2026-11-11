# Agent Documentation Refresh Implementation Plan

> Historical plan from commit `306bec8d`. For the current architecture, see the [Agent maintenance guide](../../AGENT_MAINTENANCE.md).

> **For agentic workers:** Use the executing-plans workflow task-by-task. This approved task changes documentation only; it does not authorize runtime changes, service activation or executing historical plans.

**Goal:** Make the repository's Agent maintenance guidance match the inspected source and provide a reliable starting point for future work.

**Architecture:** Reuse AGENTS.md, PROJECT_STANDARDS.md, AGENT_MAINTENANCE.md and AGENT_TOOL_CONTRACTS.md instead of adding a competing guide. Turn docs/README.md into the navigation/source map. Keep historical latest entries in place and prepend a short current handoff summary.

**Tech Stack:** Markdown, Git, PowerShell read-only source/link checks. No application imports or scientific tools are required for this documentation-only task.

## Scope and baseline

- Source baseline: da485f5; branch: docs/agent-docs-refresh-20261007.
- Original checkout has an unrelated untracked FAISS manifest; leave it untouched.
- Use the isolated documentation worktree, not main.
- Do not inspect credentials, environment values, user databases, model weights or runtime reports.
- Do not change business code, tests, dependencies, configuration or local development skills.

## Task 1: Verify sources

- [x] Read AGENTS.md, standards, architecture/tool guides, README, current handoff and CI.
- [x] Cross-check app chat profiles, planner modules, capability catalog, factory schemas and acceptance CLI options.
- [x] Record document-to-source ownership and distinguish implementation, local acceptance, merge and deployment.

## Task 2: Refresh long-lived guidance

Files: AGENTS.md, README.md, docs/README.md, docs/PROJECT_STANDARDS.md,
docs/AGENT_MAINTENANCE.md, docs/AGENT_TOOL_CONTRACTS.md.

- [x] Replace broken documentation index links with existing documents.
- [x] Document web profiles separately from harness modes and task/sandbox backends.
- [x] Synchronize typed tool inventory, model/secret boundaries and test commands with source.
- [x] Remove stale progress claims from current guides; retain safety and scientific evidence requirements.
- [x] Explain that local .agent/skills is ignored development support, not the removed runtime Skill package.

## Task 3: Preserve history and produce handoff

File: docs/handoff/latest.md.

- [x] Keep the previous latest entries in place and add a clearly marked current summary at the top.
- [x] Record scope, source baseline, changed files, actual checks, limits and next steps.
- [x] Do not turn older approvals, frozen plans or historical test counts into new authority or current results.

## Task 4: Validate and deliver

- [x] Run git diff --check and verify only the approved Markdown paths changed.
- [x] Check local Markdown links in all changed current guides.
- [x] Verify required guide sections and factory/tool table alignment.
- [x] Scan changed documentation for credential-like material without printing matches.
- [x] Recheck original checkout status; record actual results, not assumed application test results.
- [x] Make a local docs-only commit with explicit paths; do not merge main.

No pytest, Node application tests or real-model acceptance is required here because executable files are unchanged. Those commands remain documented for future code tasks and must not be reported as run in this task.
