# Activity integration blocker fixes

Date: 2026-09-09. Local fixes and synthetic validation only; independent review
and staged integration are still pending. This is not approval to publish, merge,
train, or activate any production model.

## Scope and commits

- Worktree: `D:/MedChat/molecular_chat_system_worktrees/activity-integration-blockers`.
- Branch: `codex/activity-integration-blockers`; starting commit: `942fd28`.
- Read `AGENTS.md`, `docs/PROJECT_STANDARDS.md`, and
  `docs/handoff/staged-integration-hold-20260908.md` before implementation.
- The original/source worktrees and the parent's separate Agent worktree were
  not modified. No push, PR creation, merge, or runtime activation was performed.
- Only synthetic tables, temporary source files, temporary registries, and
  deliberately non-loadable placeholder checkpoint bytes were used in the new
  tests. No private datasets, weights, credential material, or `.env` files were
  inspected. No network calls were made. Existing subprocess test helpers use
  normal inherited process environments; credentials were not inspected or logged.

| Commit | Purpose | Files |
|---|---|---|
| `68bd59f` | Reject conflicting source identity and retain evidence | `src/activity/dataset_contract.py`, `tests/test_activity_dataset_blockers.py` |
| `36fc419` | Exclude unselected prepared models from legacy discovery | `src/activity/trainer.py`, `src/activity/predictor.py`, `tests/test_activity_legacy_discovery.py` |
| `8201500` | Preserve CSV/TSV cell lexemes and verify against source text | `scripts/prepare_activity_dataset.py`, `src/activity/dataset_contract.py`, `tests/test_activity_dataset_blockers.py` |
| `0816913` | Additional family-adapter/bundle and endpoint-selection compatibility checks | Both new focused test files |

The fixes are localized diffs on the held snapshot. Do not copy these entire
final files into an earlier integration stage. The parent should split the
appropriate hunks and tests when integrating dataset/endpoint work before family
work; the final compatibility commit deliberately contains family dependencies.

## Contracts and root causes

### 1. Source identity is evidence, not a label to overwrite

Previously only structure, relation, numeric value, and units were checked;
`target_id` and `endpoint` were discarded. Twelve synthetic BuChE/pKi rows could
therefore become a training-ready PDE/pIC50 dataset.

Now, if present, each identity column is checked against the manifest before
structure processing or replicate aggregation. Mismatches produce row rejection
reasons `target_id_mismatch` or `endpoint_mismatch` (target checked first).
Blank/non-string values in present columns also reject. Absent columns retain
the declared-manifest behavior. Existing NFKC/whitespace/case normalization is
used for comparison; no alias/mapping framework was added. Source endpoints are
compared to the source declaration, not the transformed output endpoint.

The supplied identity text is retained unchanged in accepted evidence and
replicate evidence. Available supported evidence is also retained in rejected
rows, including duplicate-group rejections and explicitly requested rejected CSV
exports. Unknown extra columns remain excluded. Conflicting rows cannot pollute
the median of matching replicates. The existing quality gate still evaluates
the remaining accepted rows; one rejection does not automatically invalidate
otherwise sufficient, consistent data.

### 2. Registration is not global selection

`get_best_model()` previously returned the first registry entry if the global
selection was empty. Newer prepared classifications could displace legacy
regressions without activation.

Explicit global selection still takes precedence. Otherwise only validated
registry records with absent readiness (historical metadata) or
`scientific_readiness=legacy_unvalidated` are eligible. Prepared endpoint/family
members use `endpoint_ready` and are excluded. Endpoint selection alone is not
global selection. Registering a synthetic family bundle leaves the global and
family selections empty and preserves the legacy choice.

The predictor's three historical paths are also checked for registered metadata:
`data/activity/rg_mpnn/best_model.pt`, `data/activity/rg_mpnn/model.pt`, and
`RG-MPNN-main/vis/AURKA/AURKA.pt`. A registered prepared checkpoint is skipped
even at these names, and search continues to the next historical path. Legitimate
unregistered historical paths still return `(path, None)`, exactly as before;
the existing loader still rejects missing prediction metadata before loading
weights. No actual model loading was needed for these selection tests. Explicit
selection tests write only synthetic temporary registry state.

### 3. Source text remains authoritative

The generic CLI used pandas type inference, and snapshot validation inferred
types again based on the caller's already-parsed frame. Thus it repeated and
certified the same rounding/leading-zero loss.

Both readers now use `dtype=str` with `keep_default_na=False`. Snapshot checking
compares source-derived cell text with the supplied frame's serialized cells,
independent of the frame's inferred types. Source bytes remain retained/hashed.
An already-lossy caller frame is rejected, not silently repaired. Numeric frames
whose serialization still exactly matches source text remain supported.

CSV and TSV tests cover `4.9999999999999994`, `5`, `5.0000`, `5e0`, and IDs such as
`000123`. The real CLI preserves original values and evidence IDs through
publication and reload. The existing threshold remains **5**, with the adjacent
lower label producing class 0 and exact 5 producing class 1. Float conversion
and aggregation at the scientific boundary were not changed to a new arbitrary-
precision numerical scheme.

## TDD execution record

Every production fix followed an observed, relevant RED run. Commands below ran
in the dedicated worktree using the requested MedChat interpreter. Counts overlap
and must not be added together as unique coverage.

| Stage | RED | GREEN |
|---|---|---|
| Identity, initial 13 tests | 12 failed, 1 passed | Included in 331 passed, 2 skipped |
| Legacy discovery, initial 23 tests | 16 failed, 7 passed | Included in 185 passed |
| CSV/snapshot, 14 selected tests | 12 failed, 2 passed, 13 deselected | Included in 442 passed, 5 skipped |
| Final focused blockers including 3 added compatibility tests | Compatibility additions require no further production changes | 53 passed |

The CSV test setup had two preliminary, non-evidence runs: an invalid test
manifest declared binary instead of probability output units, and an attempted
minimal child environment prevented Windows Python hash initialization. Both
fixture issues were corrected before the effective RED run. The effective RED
then showed ten missing snapshot rejections and two CLI publications of `5.0`
instead of `4.9999999999999994`.

### Exact pytest commands already completed

Identity RED:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_activity_dataset_blockers.py -q -p no:cacheprovider
```

Identity GREEN/regression: **331 passed, 2 skipped**.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_activity_dataset_blockers.py tests/test_activity_dataset_contract.py tests/test_activity_family_dataset.py -q -p no:cacheprovider
```

Discovery RED:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_activity_legacy_discovery.py -q -p no:cacheprovider --tb=short
```

Discovery GREEN/regression: **185 passed**.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_activity_legacy_discovery.py tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py tests/test_activity_trainer_endpoint_metadata.py -q -p no:cacheprovider
```

CSV effective RED (also used for the two fixture-correction attempts):

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_activity_dataset_blockers.py -k 'lossy or exact_source_text or generic_cli' -q -p no:cacheprovider --tb=short
```

CSV GREEN/regression: **442 passed, 5 skipped**.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_activity_dataset_blockers.py tests/test_activity_dataset_contract.py tests/test_activity_prepare_cli.py tests/test_activity_family_dataset.py tests/test_activity_family_prepare_cli.py tests/test_activity_prepared_training_data.py -q -p no:cacheprovider
```

Final focused run: **53 passed**.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_activity_dataset_blockers.py tests/test_activity_legacy_discovery.py -q -p no:cacheprovider
```

### Broader activity regression

Result: **870 passed, 5 skipped, 6 deselected, 1 warning**, in 626.45 seconds.
The skips concern Windows symlink privileges. The warning is the existing
RG-MPNN conversion of a list of NumPy arrays to a tensor, not a blocker failure.

The already-started broader run used this exact command. It collected before
the three final compatibility tests were added; those additions are covered by
the 53-test focused run above.

```powershell
$activityTests = @(rg --files tests -g 'test_activity*.py' | Sort-Object)
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest @activityTests tests/test_family_training_run.py -k 'not registered_pair_runs_real_forward_without_global_selection and not real_rg_mpnn_one_epoch_on_synthetic_fixture_only and not seed_controls_nonzero_dropout_not_ambient_rng' -q -p no:cacheprovider
```

This deselects six training/forward cases. One existing pinned-predictor test
still exercises an in-memory, randomly initialized synthetic network; it does
not load private or production weights or activate registry models. This is
implementation-contract coverage, not scientific accuracy evidence.

### Static verification

Full `src` and `scripts` compileall completed with exit code 0. Bytecode was
redirected to a temporary cache, not written into repository source directories:

```powershell
$compileCache = Join-Path ([IO.Path]::GetTempPath()) ('ac-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -X "pycache_prefix=$compileCache" -m compileall -q src scripts
git diff --check
```

An initial compileall attempt used the longer temporary prefix
`medchat-activity-compile-` plus a full GUID. It failed to create three deep RG-MPNN
bytecode paths on Windows. Shortening only the temporary cache prefix resolved
this; no source changes were needed. `git diff --check` passed at every commit.

## Review/integration concerns

- Herschel's independent specification review of `942fd28..0816913` was started
  by the parent before this documentation commit; its outcome remains pending
  here. No CI/PR state was queried because this task was local/offline. Do not
  treat these results as merge authorization.
- Existing prepared datasets that lost source identities/lexemes may need
  re-preparation from their original authorized sources. No real assets were
  inspected, rewritten, repaired, or re-registered here.
- Identity-bearing prepared CSVs now carry optional evidence columns, and
  rejected exports carry available supported evidence. Downstream compatibility
  and digests must be considered when splitting the staged integration.
- Snapshot validation intentionally rejects lexically lossy numeric DataFrames,
  even when a rounded value is mathematically equal to a textual value such as
  `5.0000`. Callers should preserve input strings until validation/conversion.
- Windows symlink-permission skips are not evidence for Linux symlink behavior.
  No deployment/health/real-service acceptance was run: those would inspect
  assets/services beyond this task. No JavaScript or deployment files changed.
- Scientific model performance, production inference, and model activation are
  outside this validation. Placeholder metadata/bytes are explicitly synthetic.

## Bacon P2 follow-up: reject raw NUL before parsing

Date: 2026-09-09; review base `b9da918`, same branch/worktree. Bacon identified
that `dtype=str` does not prevent pandas' C parser from truncating cells at
actual NUL bytes. Parsing the raw snapshot the same way then certifies the
truncated values. This follow-up does not change the three earlier fix contracts.

Precisely scoped files for this follow-up:

- `src/activity/dataset_contract.py`: shared raw-byte NUL rejection, called at
  snapshot acquisition and before the snapshot verifier's pandas parser.
- `scripts/prepare_activity_dataset.py`: invoke rejection immediately after
  reading source bytes, before either CSV header or pandas parsing.
- `src/activity/family_dataset.py`: **explicitly authorized scope addition**;
  one guard before `_adapt_source` parses or re-encodes the original bytes.
- `tests/test_activity_nul_source.py`: new synthetic CSV/TSV regressions.
- `docs/handoff/activity-blocker-fixes.md`: this audit record.

Production changes are 11 added lines across the three production files. The
fixed library error is `NUL bytes are not allowed in activity source`; it contains
no source rows, suffixes, identifiers, or paths. The public generic CLI retains
exit code 2 and its fixed `contract_rejected` JSON with `ready_for_training=false`.
There is no stripping, replacement, cleaning, automatic correction, or publication
of the rejected bytes. All bytes are scanned, including headers and unused cells.

### Effective RED and GREEN

The 12-row synthetic repro carries actual `0x00` in value, compound ID, and
target ID cells (e.g. `4.8` / `000123` / `pde-family` followed by NUL and a synthetic
discarded suffix), with endpoint `pIC50`. Direct tests explicitly observe pandas
returning the truncated values before asserting snapshot rejection.

The first test-fixture attempt encountered **32 setup errors** because this
environment's CSV writer refuses NUL. The fixture was corrected to insert actual
NUL bytes after serialization; this is test construction, not production input
cleaning. No production code was changed before the next effective RED run.

The effective pre-fix run was **32 failed**, with no setup errors: generic CLI
`--prepare`/`--validate-only` incorrectly returned code 0; `_run` and public
snapshot validation did not reject; parser-order guards proved that parsing was
reached before rejection. Tests cover CSV and TSV, byte/path snapshots, headers,
value/identity/evidence/unused columns, and family validation/publication modes.

On this MedChat Python version, the family adapter's existing Python CSV reader
already raises `csv.Error: line contains NUL` while reading the body. Thus family
publication bypass was **not reproduced on this interpreter**. The new guard
makes rejection explicit, fixed, and pre-parser rather than relying on that
version-specific parser behavior before adapter byte rewriting.

After the minimal fix, the same command produced **32 passed**:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_activity_nul_source.py -q -p no:cacheprovider --tb=short
```

Related regression: **475 passed, 5 skipped** in 156.90 seconds. The five skips
are the existing Windows symlink-privilege cases, not NUL rejection failures.
Command:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_activity_nul_source.py tests/test_activity_dataset_blockers.py tests/test_activity_dataset_contract.py tests/test_activity_prepare_cli.py tests/test_activity_family_dataset.py tests/test_activity_family_prepare_cli.py tests/test_activity_prepared_training_data.py -q -p no:cacheprovider
```

Changed Python files compiled successfully with a temporary bytecode cache;
`git diff --check` also passed:

```powershell
$compileCache = Join-Path ([IO.Path]::GetTempPath()) ('ac-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -X "pycache_prefix=$compileCache" -m compileall -q src/activity/dataset_contract.py src/activity/family_dataset.py scripts/prepare_activity_dataset.py tests/test_activity_nul_source.py
git diff --check
```

All follow-up inputs/files were synthetic and temporary. No real assets,
credentials, environment files, network, training, or model activation were used.
No other worktree was modified. Bacon's follow-up review remains pending; no
push, merge, or review approval is implied by GREEN.
