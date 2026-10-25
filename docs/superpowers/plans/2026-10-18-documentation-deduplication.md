# Documentation Deduplication Implementation Plan

> **For agentic workers:** Use the existing review workflow and validate each documentation batch before committing it.

**Goal:** Reduce repeated project and deployment guidance while keeping one discoverable authoritative source for each operational rule.

**Architecture:** Keep `README.md` as the short project entry point, `docs/README.md` as the documentation map, `docs/PROJECT_STANDARDS.md` as the engineering contract, and `deployment/README.md` plus `docs/DEPLOYMENT_TUTORIAL.md` as the deployment reference and procedure. Historical handoffs, plans, specifications, reports, and runbooks remain evidence records unless a link target must be repaired.

**Tech Stack:** Markdown, existing repository links and anchors, Python/pytest documentation contract tests.

---

### Task 1: Establish the documentation source map

**Files:**
- Modify: `docs/README.md`
- Review: `README.md`, `AGENTS.md`, `docs/PROJECT_STANDARDS.md`, `deployment/README.md`, `docs/DEPLOYMENT_TUTORIAL.md`

- [ ] Record which document owns setup, engineering rules, deployment concepts, deployment procedure, and historical evidence.
- [ ] Replace repeated long explanations in the index with short routing descriptions and stable links.
- [ ] Verify every new link and anchor locally.

### Task 2: Remove repeated operational instructions

**Files:**
- Modify: `README.md`, `AGENTS.md`, `deployment/README.md`, `docs/DEPLOYMENT_TUTORIAL.md`

- [ ] Keep commands and security boundaries in the document that owns them.
- [ ] Replace duplicate command blocks with links to the authoritative section.
- [ ] Preserve current main-model configuration, Temporal trust boundary, full-source prewarm, RDKit startup, and single-worker chat-docking requirements.

### Task 3: Validate the documentation-only change

**Files:**
- Test: existing Markdown link and deployment contract checks

- [ ] Run `git diff --check` and the existing focused documentation tests with an external temporary directory.
- [ ] Run a local Markdown file/anchor scan over tracked documentation.
- [ ] Run an independent review of the final diff for lost requirements, stale links, and remaining high-value duplication.
