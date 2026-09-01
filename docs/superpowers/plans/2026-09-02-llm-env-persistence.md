# LLM Env Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist UI-entered LLM credentials in the ignored local `.env` while keeping only placeholders in Git.

**Architecture:** Extend the existing runtime-config helper with an atomic `.env` updater, then call it from the authenticated save/switch routes. Startup continues using the existing environment loader, so no new secret store or encryption dependency is introduced.

**Tech Stack:** Python 3.10+, FastAPI, pathlib/os, pytest, vanilla JavaScript.

---

### Task 1: Add failing `.env` persistence tests

**Files:**
- Modify: `tests/test_llm_runtime_config.py`
- Modify: `tests/test_admin_auth_routes.py`

- [x] Test writing OpenAI-compatible credentials while preserving comments and unrelated variables.
- [x] Test blank-key preservation, explicit deletion, duplicate-key cleanup, and newline rejection.
- [x] Test the authenticated save route writes to `MEDCHAT_ENV_FILE` without returning the Key.
- [x] Run focused tests and verify RED failures.

### Task 2: Implement atomic local environment persistence

**Files:**
- Modify: `src/web/llm_runtime_config.py`

- [x] Add provider-to-environment-key mapping.
- [x] Add an atomic updater that preserves unrelated lines and rejects multiline values.
- [x] Add `save_llm_env_config()` implementing save/keep/clear semantics.
- [x] Add provider markers, provider-specific recovery, process synchronization and write locking.
- [x] Run helper tests and verify GREEN.

### Task 3: Integrate save and startup paths

**Files:**
- Modify: `src/web/app.py`
- Modify: `.env.example`

- [x] Resolve the runtime env path from `MEDCHAT_ENV_FILE`.
- [x] Persist before applying saved LLM configuration.
- [x] Teach ModelScope startup to read its environment variables.
- [x] Add an explicit UI control for clearing locally saved keys.
- [x] Keep all public responses secret-free and update UI success wording to state local persistence.
- [x] Run route tests and verify GREEN.

### Task 4: Full verification and restart

**Files:**
- No additional production files.

- [x] Run focused runtime-config and admin-route tests.
- [x] Run Agent and platform regression tests.
- [x] Run `python -m compileall -q src scripts` and frontend checks.
- [x] Verify `.env` remains ignored and `.env.example` contains placeholders only.
- [x] Restart the local service and verify public config reports `api_key_configured: true` without a Key field.
- [x] Explicitly stage task files, excluding the FAISS manifest, and commit.
