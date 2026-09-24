# Package 4D: contract inventory and restricted helper boundary

Baseline e173d43; branch codex/tool-contract-inventory. Recommended choice delegated by the active packages1–8 goal. This is documentation/test-only completion of package4, not a new execution capability.

Design: inventory the ten canonical model capabilities from capabilities/catalog.py and four registered staged docking helpers from tooling/factory.py. All scientific/retrieval capabilities must use explicit input/output models, not the generic LegacyQueryInput/None pair. Retain exactly four justified compatibility exceptions: prepare_receptor, prepare_ligand, run_docking, get_docking_result. Their string execute path must never call the domain service or yield scientific success; direct structured .run methods remain trusted domain APIs, not model actions. RXN remains explicitly NON_WORKFLOW_TOOLS, RAG alias maps to one canonical entry. Do not alter factory, permissions, tools, scores, scientific validators or production configuration.

Alternatives rejected: typing generic helpers as if executable would imply unsupported permissions; removing public helper APIs breaks compatibility; testing only class names would miss service dispatch. Use the actual factory and actual helper execute methods with a service trap, plus authorized_catalog checks.

TDD plan:

1. Read registry/factory/capability and existing docking/registration tests. Add tests/agent/test_tool_contract_inventory.py covering complete inventory, explicit models, alias/exclusion, actual helper refusal/no service access and no advertised model capability even when requested by allowlist.
2. Run on current main. Expected RED only for four still-unmerged target/reverse/generator/ranker contracts; record actual results, not a dependency skip or a scientific pass.
3. After reviewed 4B/4C merge, integrate main and run the same tests unchanged. No test expectation weakening or production changes to make them pass.
4. Add docs/AGENT_TOOL_CONTRACTS.md with exact models/modules and justified helper exceptions at final integrated revision. Run existing registry/registration/docking/decision authorization tests as relevant. Independent SPEC/QUALITY and exact-head CI before publication.

No models, secrets, live assets, server or deployment. The constructor fixtures prove registration shape, not real scientific results. Helper service traps prove refusal in the string Agent path, not validation of every direct domain .run implementation.

RED via existing isolated MedChat runner, tests/agent/test_tool_contract_inventory.py on e173d43:4 failed12 passed1.47s, exit1. Exactly target_database_search, reverse_target_predictor, llm_molecular_generator, candidate_ranker still have generic input/output boundaries on this main. Helper no-service/refusal and alias/exclusion checks passed. Wait for independently reviewed4B/4C integration; do not skip these requirements or duplicate their implementation here.

## Integrated GREEN and review checkpoint

4B PR #69 and 4C PR #73 were independently reviewed and merged after exact-head CI 8/8, no unresolved reviews, with reviewed/merged tree equality. Fast-forwarded this independent branch first to main `27170d9`, then to `0295e9960b15c850bb212e434da66824181ef546` after #73. Original inventory test file was not changed: no skips, weaker schema checks or local factory override.

The same isolated MedChat runner from `2026-09-24-rag-service-extraction.md`, with only worktree path and explicit test arguments changed, ran these four complete files: `test_tool_contract_inventory.py`, `test_tool_registry.py`, `test_registration_consistency.py`, `test_docking_tool_contract.py` (all `tests/agent`): **310 passed, 7 existing SWIG/FastAPI deprecation warnings in 6.23s**, exit 0. It uses temporary runtime/config roots and offline fixtures, not live tools or user assets. Inventory itself contains 16 cases; all are included in that run. Full Agent is not required locally for this test/document-only batch; latest exact-head CI remains a separate gate, and no whole-repository/live acceptance is claimed.

Added `docs/AGENT_TOOL_CONTRACTS.md` with the actual ten input/output models, one RAG alias and four deliberately restricted helper exceptions. Updated `docs/handoff/remaining-through-step8.md` with verified PR69–73 progress without declaring packages6–8 done. Scope is those three documents plus the inventory test. No production source, permissions, adapters, scientific contracts, configuration or algorithms change in 4D. Independent SPEC then QUALITY review and publication remain pending.

Separately repeated the exact original inventory path: **16 passed in 1.03s**, exit 0. In-memory compilation of the new test and `git diff --check` passed. No failing or skipped case was removed between RED and GREEN; integration of reviewed typed contracts is the only reason the four original failures now pass.
