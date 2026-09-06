# MedChat Platform V1 Baseline PR

## Scope

This draft PR consolidates the final MedChat platform tree while the complete original development history remains reachable from a local archive branch and annotated tag. It does not merge or rewrite `main`, and it does not include the dirty original worktree's uncommitted files.

## Source identity

- Source branch: `codex/target-search-authoritative-fallback`
- Source commit: `f6e9e36ed00b470fbe16ddd59b4b05e81d9c775b`
- Remote-main commit: `ed8c150a6000abed4a57661b09b90c7f3121e7ec`
- Local-main/documentation base: `3b87853066069231891bad09efefd165ecf5af26`
- Baseline snapshot commit: `ca08f78`
- Baseline gate-stability commit: `7dc748d`
- Local archive branch: `codex/archive/platform-stack-2026-09-06`
- Local annotated archive tag: `archive/platform-stack-2026-09-06`

The archive branch and tag are intentionally not pushed until the historical credential-like test fixtures receive a separate history audit.

## Included systems

- Agent scientific contracts, orchestration, persistence, provenance and evaluation
- LangGraph shadow/canary harness
- Temporal durable docking runtime and deployment assets
- OpenSandbox docking broker, isolation and stability hardening
- Web model configuration, safe rendering and workflow completion fixes
- Authoritative target lookup, expiring evidence cache and structure retrieval
- Local Ollama molecular generation binding and deterministic candidate ranking

## Verification

- Python compilation: passed with `python -m compileall -q src scripts`.
- Agent suite: 1505 passed, 1 opt-in external test skipped.
- Full deterministic Python suite: 6167 passed, 233 skipped, 171 subtests passed, 8 dependency/deprecation warnings.
- Node suite: 8 tracked JavaScript tests passed.
- Contract acceptance: 34/34 passed, pass rate 1.0.
- Credential scan: exact CI pattern returned no match (`git grep` exit 1).
- New large files: 0 baseline-added files exceed 10 MiB.
- Forbidden tracked runtime paths: 0; no real `.env`, SQLite runtime database, model weight, output, scratch report or FAISS manifest is tracked.
- Original-worktree state: 13 status entries before and after; SHA-256 remained `adb459051c62b3f3e2e17df4f7b81186aae4ba63400a5a7bbee0f5c854d8f465`.
- Diff whitespace check: passed.

The first full-suite run exposed two test-environment defects: a concurrent capacity test assumed deterministic coroutine winner indices, and an LLM configuration test inherited the host provider marker. Both test contracts were corrected without changing production behavior. The focused fixes passed, the concurrency test passed 20 consecutive runs, and the full suite then passed.

## Golden real acceptance

Command:

```powershell
python scripts/run_agent_acceptance.py --mode real --case-set golden --repeat 1 --output scratch/platform-v1-golden-real.json
```

Result:

- Overall status: `partial`
- Cases: 12
- Passed: 7
- Partial: 5
- Failed: 0
- Skipped: 0
- Non-catastrophic pass rate: 1.0
- p50 latency: 107.9 ms
- p95 latency: 6438.633 ms
- External main model: passed, model `gemini-3.1-pro`, API key present at runtime only
- Local molecular generator: passed, model `gmm-llama:latest`
- Authoritative target search: passed
- Real docking: passed; 3 poses, best binding energy -3.6, pose artifact verified
- Activity model inference: failed honestly; no real RG-MPNN model path was available and no simulated pIC50 was emitted

The PDE5A target-driven design case generated 10 requested candidates with 10 valid and 10 unique SMILES. Target evidence, generation gating, property consumption, candidate identity, scientific-claim evidence and compiled data flow all passed. Candidate ranking completed without fabricated docking energy.

## Honest partial outcomes

- `GOLD-003`: partial because RG-MPNN evidence and reverse-target evidence were unavailable; target-structure binding could not continue after the reverse-target output was absent.
- `GOLD-006`: partial because `data/reverse_target/chembl_training_data.tsv` is absent.
- `GOLD-007`: partial because the reverse-target workflow could not execute without its training data. The report currently assigns score 100 to this honest low-confidence rejection while retaining `status=partial`; this scoring inconsistency remains a known evaluation issue.
- `GOLD-008`: partial only because no real RG-MPNN prediction was available; all target, generation, property and ranking data-flow gates passed.
- `GOLD-009`: partial because no real RG-MPNN prediction was available.

No case fabricated pIC50, binding energy, docking score, literature or experimental conclusions.

## Review focus

- Scientific provenance and anti-hallucination gates
- Durable task and sandbox trust boundaries
- Deployment defaults and migration risk
- Frontend structured result rendering
- Target-source cache lifetime and remote evidence semantics
- The known `partial` plus score-100 acceptance inconsistency

## Rollback

Do not merge this draft automatically. If the baseline is later merged and must be rolled back, create a normal revert PR; never force-reset `main`. The complete pre-baseline history remains available from the local archive branch and tag.
