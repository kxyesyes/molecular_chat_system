# Reverse-target popcount validation: focused integration

## Scope and provenance

Base: actual main squash29502ca560ae1844f98290f84d85f2ab6b6fc750.
Branch: codex/reverse-popcount-integration. Extract only predictor.py, its new
popcount tests and the historical design/plan from9a3ccd3. The predictor baseline
at that commit's parent is identical to this main baseline; final predictor/test
blobs equal a4598a87028c45a3ad202e8267b582b67a05f036 and
d7aad18c8eb1f03f57a8b97ae3d272e05f17b0a2 respectively. No accumulated branch merge.

This slice validates cached row bit counts and canonicalizes validated counts to
uint16 to prevent narrow-integer Tanimoto overflow. Scientific scoring, thresholds
and sort/group behavior are unchanged. Corrupt existing caches fail safely without
rewriting; missing-cache best-effort computation remains compatible. It does not
prove database row alignment, stable source ownership or real-model accuracy.
Historical RED/GREEN evidence remains in the extracted plan; no redundant source
change solely to manufacture another fix. Fresh integration gates follow.

## Verification and release gates

Run from this worktree, one local test process at a time:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_popcounts.py tests/test_reverse_target_health.py tests/agent/test_reverse_target_complete_input.py tests/agent/test_target_tool_contract.py
```

Isolated launcher SHA256:
857D6477FE141931128D3BC4B87297F027406A95CF8CC8FB40F006998A79612F.
Only its REPO literal differs from the approved isolated launcher. Temporary
synthetic test assets only, no host data/config/secrets/network/model activation.

Fresh parent union:394 passed,2 subtests passed,8.51s,exit0,terminal3acdeb.
No warnings/skips reported. Independent Socrates SOURCE/SPEC approves with no
actionable findings. Independent Galileo QUALITY repeated the exact union once:
394 passed,2 subtests passed,7.40s,exit0,terminal34d896; no warnings/skips.
Source/test/runner and three documents unchanged before/after, diff check clean.
After approval: compile both Python files in memory, diff checks, exact file
staging and scoped credential scan; publish one attached draft PR. Require all
exact-head CI gates and zero unresolved review findings before delegated squash
merge. Deployment and live acceptance are not part of this slice; P7/P8 remain.
