# Target contracts implementation plan

Use TDD with independent SPEC then QUALITY. Write set: src/agent/tooling/target_contract.py, factory.py target/reverse wiring, tests/agent/test_target_tool_contract.py and narrowly documented legacy fixture corrections. Producer behavior changes beyond the approved adapter mapping require a separate batch.

1. Characterize actual target/reverse producers and existing TargetEvidenceValidator, ToolResult, legacy worker/compat and activity contract pattern. Read existing target tests without reading real databases/cache/config. Baseline tests/agent/test_domain_result_validators.py, test_target_identity_alignment.py, test_target_selection_execution.py, test_tool_registry.py and tests/test_target_search.py with isolated runner.
2. Add counted real-wrapper tests proving resolved/not_found currently fail registry normalization. Add assertions for ambiguous/unavailable/partial, query container preservation and strict invalid inputs. Run RED before production edits.
3. Add strict non-projecting input/output views and target-local status mapping. Use original raw validator first, compat on a completed-invocation proxy, preserve warnings/evidence/artifacts/quality and lookup metadata. Follow the exact worker/deadline and failure-snapshot safeguards in the design. Factory selects schemas/adapters only for the two names; no global adapter changes.
4. Adversarial matrix: missing/null/extra known fields, numeric bool/string/NaN/Inf, negative counts, out-of-range similarity, nullable unknown structure_count, contradictory flags/status, nonempty not_found, empty resolved, metadata conflicts, original raw object unchanged, canonical failure embedded malformed raw, depth/cycles, caller validator exceptions, retry false, timeout and close. Execute RED/GREEN per discovered boundary. Use real producer shapes, not invented success DTOs.
5. Preserve entire real producer observation on conversion except elapsed and explicit status/lookup metadata; preserve direct producer API unchanged. Run focused tests and full Agent suite in the isolated MedChat runner; test minimum Pydantic2.5 using the already retained dependency target only if available. Never install globally/read keys/enable real services.
6. Append real commands/results, inspect diff, memory-compile Python, independent SPEC then QUALITY. Exact scoped commit, independent draft PR, latest all-required CI and no unresolved reviews before authorized squash. Do not claim package4 complete until generator/ranker and restricted helper audit also complete.

## Worker implementation evidence (2026-09-25)

### Scope and isolation

- Worktree: `D:/MedChat/molecular_chat_system_worktrees/target-tool-contracts`; branch `codex/target-tool-contracts`; starting HEAD `4238d4efbacf671c1205b93e021ff9971fac13e0`, base `aa86377`. Started clean. No commit, staging, push, PR or merge is authorized for this worker.
- Write set: new `src/agent/tooling/target_contract.py`, target/reverse-only wiring in `src/agent/tooling/factory.py`, new `tests/agent/test_target_tool_contract.py`, two exact expected-quality assertions in `tests/agent/test_tool_registry.py`, and this plan. No producer/parser/global adapter/other worktree changes.
- The two existing registry assertions now additionally require `lookup_status` and, where supplied, `lookup_path`. All existing provider-error, retry-count, source, stale, warning and evidence assertions remain. No downstream scientific/safety gate assertion was removed or bypassed.
- Read the retained-profile instructions in `domain-api-separation/tests/fixtures/api_route_contract_profiles.md` before use. No dependency installation or cleanup of that retained profile occurred. The subprocess explicitly reported Pydantic **2.5.0** / FastAPI **0.104.1**.
- Every pytest invocation uses the complete `$runner` from `2026-09-24-rag-service-extraction.md`, replacing only its worktree with this worktree. It retains the six-variable non-secret environment allowlist, temporary cwd, isolated configuration/session/Agent/Task/target DB/cache paths, disabled real/canary switches, normal conftest and three hash-checked tracked evaluation JSONL fixtures. No real `.env`, user configuration, model/index/database assets or external services were used. Tests are offline contract evidence, not scientific acceptance.
- Host interpreter: the same MedChat Python named in the RAG runner, invoked with `-B -c "import sys; exec(sys.stdin.read())"`. Its child runs `-B -m pytest <absolute test paths> -q -p no:cacheprovider --tb=short -rs`. The minimum profile only prepends the already retained `medchat-domain-api-profiles-20260925-b831/ci` directory to the child's `PYTHONPATH`; the host environment is unchanged.

### Boundary implementation

- Strict validation views are discarded, never serialized over transport dictionaries/tuples/opaque extensions. Input unwraps only the existing query envelope; direct target records/lists/tuples preserve their identity and producer-owned precedence, normalization, deduplication and five-query cap. Reverse input remains string/query-wrapper only.
- Raw dictionaries alone get target-local lookup mapping after the original callback has seen the original raw value once per invocation. `resolved` requires nonempty records, `not_found` requires no records, and existing lookup metadata must agree with status/data/path. `unavailable`/`partial`, service statuses, retryability, unknown counts, source/stale metadata and clarification/provider formatted text survive.
- Known record/source/structure/assay fields, finite similarities, counts, identities and complete envelope fields are checked on success, partial and failure. Missing optional assay measurements are not fabricated. Explicit null reverse similarity/count/assay slots are rejected; unknown structure counts remain nullable. TargetEvidenceValidator is reused without asserting scientific authenticity.
- Only `error.details.raw_result` chains are followed: up to 16 embedded snapshots, cycle rejection, no recursive validation of opaque extensions. Invalid contract errors contain no input/output payload.
- Producer, caller callback, raw checks, target mapping, existing `execute_tool_compat`, inherited data redaction and canonical checks all execute inside the existing single worker/deadline/slot. The target-local outer `_normalize` only attaches framework elapsed time; `_validate_output` only returns the result. No mutable validation marker or generic hook. Caller exceptions remain outside domain-validation exception handling. Retry policy, lazy health and close ownership are unchanged.

### TDD and regression runs

`BASELINE`: `tests/agent/test_domain_result_validators.py`, `test_target_identity_alignment.py`, `test_target_selection_execution.py`, `test_tool_registry.py`, plus `tests/test_target_search.py`.

`FOCUS`: `BASELINE` plus `tests/agent/test_target_tool_contract.py`, `test_tool_adapters.py`, `test_tool_adapter_compat.py`, `test_activity_tool_contract.py`, `test_rag_tool_contract.py` (the latter four paths are under `tests/agent`).

| Run | Observed result | Exit |
|---|---|---|
| Baseline, before edits | 184 passed, 3.89s | 0 |
| First actual-wrapper/input RED | 20 failed, 3 passed, 1.05s; actual resolved/not_found became failed | 1 |
| Initial adversarial expansion | 99 failed, 5 passed, 2.08s; included one test setup error assigning into null details | 1 |
| Corrected adversarial RED | 91 failed, 13 passed, 2.03s; fixture initializes details explicitly; payload-free assertions accept existing null details | 1 |
| Initial contract GREEN | 104 passed, 1.37s | 0 |
| Initial FOCUS | 2 failed, 897 passed, 8.54s; both failures were exact quality expectations missing newly required lookup metadata | 1 |
| Minimum profile, initial contract | 104 passed, 1 existing warning, 1.39s | 0 |
| Metadata/null/lifecycle expansion | 16 failed, 112 passed, 1.64s; four tests initially assigned a frozen ToolSpec field | 1 |
| Corrected metadata/null RED | 12 failed, 116 passed, 1.64s; timeout is configured on fixture before registry construction | 1 |
| FOCUS after metadata checks/expected-quality updates | 923 passed, 8.38s | 0 |
| Minimum profile, 128-case contract | 128 passed, 1 existing warning, 1.57s | 0 |
| Source metadata/stable identifier RED | 5 failed, 156 passed, 1.97s | 1 |
| FOCUS after source/identifier checks | 956 passed, 11.26s | 0 |
| Normalization/deadline RED | 2 failed, 4 passed, 3.35s; inherited outer redaction blocked 2 seconds outside a 0.03-second deadline and its returned data escaped revalidation | 1 |
| FOCUS with worker-local redaction | 958 passed, 10.83s | 0 |
| Minimum profile, worker-local redaction FOCUS | 958 passed, 1 existing warning, 11.57s | 0 |
| Existing target-search fallback suite | 251 passed, 2 skipped, 22.25s | 0 |
| First full Agent (intermediate snapshot) | 5761 passed, 2 skipped, 7 warnings, 293.39s | 0 |
| Contract with eventual slot-release assertion | 163 passed, 3.12s | 0 |
| Minimum profile FOCUS including slot-release assertion | 958 passed, 1 existing warning, 10.15s | 0 |
| Complete-envelope elapsed metadata RED | 8 failed, 1.41s; bool/negative/string/infinite supplied elapsed accepted by raw/canonical envelopes | 1 |
| FOCUS with strict elapsed metadata | 966 passed, 10.24s | 0 |
| Minimum profile FOCUS with strict elapsed metadata | 966 passed, 1 existing warning, 10.17s | 0 |
| Second full Agent (before elapsed field addition) | 5796 passed, 2 skipped, 7 warnings, 270.55s | 0 |
| Final frozen-snapshot full Agent | **5804 passed, 2 skipped, 7 existing warnings, 274.52s** | **0** |

The first full Agent process was already running when source/normalization tests were extended; the second was already running when the missing elapsed field validation was found. Both are deliberately recorded as intermediate snapshots, not final-code evidence. The final full Agent and both 966-case FOCUS runs correspond to the exact frozen four-file code/test snapshot below. The new contract file contributes 171 test cases. No code/test changes occurred during that final full run; only this evidence document was updated.

Existing skips: Agent directory symlink capability and disabled performance test; fallback suite real authoritative lookup disabled and Windows symlink privilege unavailable. Existing warnings: SWIG type metadata and FastAPI on_event deprecations; the minimum profile additionally emits the existing `model_provenance` protected-namespace warning. No skip/xfail/warning filters were added.

### Frozen implementation snapshot and static checks

All four changed/new Python files compiled in memory. Additionally all **313 tracked Python files under src/scripts** compiled in memory; no pyc was written. `git diff --check` passed. A before/after AST comparison of `factory.py`, removing only the new target-contract import and target/reverse conditional branches, was identical. `git diff HEAD -- src/agent/tools src/agent/tooling/adapters.py src/agent/contracts/scientific.py` was empty. The staging index remained empty and HEAD did not move.

The following code/test snapshot excludes this evidence document to avoid a self-referential hash. Aggregate algorithm: sort paths, join `path + " " + sha256(raw_bytes)` with LF including the final LF, SHA-256 that UTF-8 text.

```text
src/agent/tooling/factory.py cc2e4100acda775ffaf2c42140e496b4eaeb0e11bca96d3ca9ccfd2221fad0fa
src/agent/tooling/target_contract.py 097a53beecbebe8615eac9e6c8bca14b2670c7d8a6fac9b2dce07688e39f59d7
tests/agent/test_target_tool_contract.py 96f6e7eef61fa9e1ab5897111b9267fc760544ae7c95c047134234817685c473
tests/agent/test_tool_registry.py 10ef087e417686b9443bbdce6514e8f6a727be2485288e398b83029f0dbddda9
```

Aggregate `ee025b716287e395ad64a959b33fe03339217ff1d58e7b93927d59d63f6d4866`.

No JavaScript/deployment/data changes: Node checks, production health checks and real scientific service acceptance were not run. This is not a full-repository or Linux CI claim.

### Review and integration boundary

- This worker's self-check is not independent SPEC/QUALITY approval. Parent must arrange independent SPEC then QUALITY and integrated regression before commit/PR/CI/merge.
- No analysis4A or whole-input reverse producer changes were copied. Parent integrates their separately reviewed revisions. For the two downstream seal/publication test modules mentioned by the parent, deliberately contradictory observations should use a test-local generic LegacyPythonToolAdapter, not another temporary scientific tool or relaxed production validation; those files are untouched here.
- This does not complete all package4–8 work, a deployment, real scientific validation or authorized CI/merge work.
- Worker handoff: implementation/local verification complete. Final write set remains exactly the five paths listed above; index empty, HEAD `4238d4efbacf671c1205b93e021ff9971fac13e0` unchanged. Independent review, integration with analysis/whole-input changes, remote CI and merge remain pending with the parent. No known failing test remains in the current scoped baseline; combined-branch compatibility has not been claimed.

## QUALITY P2 follow-up: canonical elapsed-time ownership

The parent reported independent SPEC approval (966 host / 966 minimum-profile cases) for snapshot `ee025b716287e395ad64a959b33fe03339217ff1d58e7b93927d59d63f6d4866`. QUALITY/Ptolemy then reproduced a P2 timing regression: canonical producer ToolResult objects with missing elapsed time were stamped by `execute_tool_compat` with the already-completed proxy's near-zero duration. The previous dict-only reset prevented the outer framework from supplying the full producer duration. All other findings were reported clear. This section does not claim QUALITY re-approval.

### Bounded fix and tests

- Only `target_contract.py`, its existing test file and this evidence document changed in this follow-up. No generic adapter/compat/producer changes, branch integration, staging, commit or push.
- Capture canonical `elapsed_ms` immediately before compat, then restore it immediately afterward, including the missing `None` sentinel and explicit `0`. Raw dictionaries keep their existing framework-owned timing semantics. The canonical producer object remains the same object; legacy in-place attachment of the final framework duration remains unchanged.
- Added a 48-case controlled-clock matrix: two tools × raw/canonical × success/failure/partial-success/partial-failure × `None`/`0`/`37`. Producer advances a binary-exact 125 ms and caller validation advances 62.5 ms. The real LegacyPythonToolAdapter is the comparison oracle (generic input/output schema only in this test-local comparator). No sleeping or fragile wall-clock bounds.
- Tests also assert original callback identity/value, one producer call, unchanged raw dictionaries, canonical object identity/other fields, and retention of `None` through worker-side normalized validation until outer framework timing. They distinguish missing elapsed from an explicit zero.
- Same isolated RAG runner, worktree, whitelist environment, disabled real switches, temporary cwd and retained minimum dependency profile as above; no external services, secrets/assets or dependency installs.

| Follow-up run | Observed result | Exit |
|---|---|---|
| New controlled-clock test before fix (RED) | **8 failed, 40 passed**, 1.31s; all missing-elapsed canonical variants reported `0` instead of Legacy `187` ms | 1 |
| Same matrix after minimal fix (GREEN) | **48 passed**, 1.09s | 0 |
| Post-fix host FOCUS | **1014 passed**, 8.97s | 0 |
| Post-fix Pydantic 2.5.0 / FastAPI 0.104.1 FOCUS | **1014 passed**, 1 existing warning, 9.41s | 0 |
| Post-fix full Agent, frozen follow-up snapshot | **5852 passed, 2 existing skipped, 7 existing warnings**, 260.21s | 0 |

RED/GREEN node: `tests/agent/test_target_tool_contract.py::test_elapsed_preserves_legacy_producer_scope_and_object_semantics`; FOCUS is the same ten-file list defined above. All use the same `-q -p no:cacheprovider --tb=short -rs` child pytest options. Post-fix full Agent used `tests/agent` and the frozen follow-up snapshot below. No code/test changes occurred while it ran; only this evidence document changed. Existing skips remain directory symlinks unavailable and performance test disabled; no new skips or warning filters. The contract test file now contributes 219 cases.

Post-fix static checks: all four changed/new Python files and all 313 tracked src/scripts Python files compiled in memory; `git diff --check` passed; generic `adapters.py` and `base_tool.py` diff against HEAD remained empty. Follow-up code/test snapshot (same aggregate algorithm as above):

```text
src/agent/tooling/factory.py cc2e4100acda775ffaf2c42140e496b4eaeb0e11bca96d3ca9ccfd2221fad0fa
src/agent/tooling/target_contract.py f64f1d647d6f9574a144a5d7b11855a76c5452a8e5c3973e174aef8ee54f4f8e
tests/agent/test_target_tool_contract.py 36319cfe5ffbb1fb6db100f6ee29b626f8dc7431b6b1f4b0d1218fb30b4059fa
tests/agent/test_tool_registry.py 10ef087e417686b9443bbdce6514e8f6a727be2485288e398b83029f0dbddda9
```

Follow-up aggregate: `157e7b89321ac47b63292381d93f2c0eb4934bade3a8376cb886bde2ebda7493`.

Pending: QUALITY re-review by the parent's reviewer (`01a0d51f-37c6-7492-ac7a-a2a58467818b`) and parent-owned main integration/CI. Prior SPEC approval applies to the prior snapshot; do not silently transfer it to an unreviewed fix.

Follow-up handoff: P2 fix and requested offline verification complete. Branch remains `codex/target-tool-contracts`, HEAD `4238d4efbacf671c1205b93e021ff9971fac13e0`, index empty. No worker commit/push/merge or other-branch integration occurred. Final code/test hash was rechecked after full Agent completed and still matches the follow-up aggregate above.

Parent publication checkpoint: independent QUALITY re-review approved the frozen P2 delta, repeating1014 host and1014 minimum-profile tests plus24 additional controlled-clock probes per profile. Independent SPEC delta re-review approved, inspected the48-case matrix and verified the frozen hash without claiming another run. No unresolved findings remain at this snapshot. Latest-main/4A/6A integration and combined regression/CI are still parent-owned and required before merge.

## Reviewed-main integration and explicit downstream test fixtures

Committed reviewed implementation9545118 and merged reviewed main1bba025 (PR64/65/66/67) without conflicts. First combined scope target contract/registry/analysis/reverse whole-input/decision merge blockers/dynamic session:799 passed20.24s. Full Agent:11 failed6307 passed2 skipped7 warnings295.74s. Exact same11 failures reproduced by test_decision_spec_findings.py +test_decision_migration_boundaries.py:11 failed151 passed28.04s.

All11 are deliberate success-plus-error generic observations previously renamed to target_database_search by 4A so they could reach independent downstream seal guards. The now-typed target boundary correctly rejects their non-target payload/contradiction before the tested downstream boundary. Do not relax target validation or rename another untyped tool. Added test-only setup_loop legacy_tools opt-in: derive unchanged registration policy/spec, explicitly construct LegacyPythonToolAdapter with LegacyQueryInput/output_schema=None only for named generic probes; all other fixtures remain typed. No resources/invocations have started during this construction and the final fixture registry retains single tool-close ownership. The11 tests keep their original names, error/immutability/call-count/no-science assertions and default typed consumers remain unchanged.

Post-fixture focused GREEN: these two files +test_decision_loop.py +test_target_tool_contract.py:458 passed38.75s, exit0. Production contract unchanged after its reviews. Full integrated GREEN and independent fixture-delta review are still pending; RED retained rather than relabelled as passed.

Integrated full GREEN on main1bba025 plus reviewed4B and explicit fixtures:6318 passed2 existing skipped7 existing warnings292.59s, exit0. Independent QUALITY delta approved with its own458 passing focused tests and direct ownership/typed-default assertions; factory merge is additive, target_contract.py matches approved P2. No original scientific or seal assertions weakened. Snapshot PR #68 has separately merged at e173d43 and must be integrated before publication; native Linux CI remains required on the resulting exact head.
