# Docking Interaction Geometry and Local Acceptance Design

## Goal

Complete the locally verifiable part of the production-acceptance objective by
preserving tool-derived interaction geometry from ProLIF, keeping unavailable
scientific evidence fail-closed, and recording an explicit local-only
acceptance boundary for the remaining deployment gates.

## Scope

### In scope

- Extend the docking interaction adapter to consume ProLIF metadata for a
  selected ligand pose, including finite distances, angles, and atom-index
  mappings when the installed analyzer provides them.
- Normalize only documented/tool-derived metadata into the existing safe DTO;
  reject malformed, non-finite, or ambiguous geometry rather than inventing a
  distance or angle.
- Preserve existing interaction identity/count behavior for compatible output,
  while adding an explicit overall `geometry_status` and per-interaction
  geometry fields where available.
- Add focused tests for geometry extraction, malformed metadata, missing
  dependencies, and the existing fail-closed PDBQT/input behavior.
- Run the complete local acceptance matrix in the RDKit-enabled `MedChat`
  environment and update handoff evidence with exact commands and limitations.

### Out of scope

- Claiming target-server HTTPS/WSS, reverse-proxy, Temporal, production
  restart, or multi-worker evidence from this Windows workstation.
- Replacing ProLIF with a hand-written distance-only or RDKit heuristic
  interaction classifier.
- Adding ProLIF/MDAnalysis to the mandatory base dependency profile without a
  separate dependency decision; absent optional dependencies remain an honest
  `unavailable` result.
- Modifying the untracked user files `scripts/search_family_activity_models.py`
  and `tests/test_family_training_search.py`.
- Reworking the already merged fixes for target-search SQL binding, docking
  input mutual exclusion, stale Vina artifacts, empty pharmacophore results,
  ADMET evidence, resource budgets, WSS URL selection, or legacy log parsing.

## Design

The adapter will use the topology-bearing receptor and ligand artifacts that
are already required for interaction analysis. For current ProLIF versions,
the analysis path will request metadata (or read the populated `ifp` metadata)
instead of projecting only a count dataframe. A normalized interaction record
may contain:

- interaction type and residue identities;
- a positive finite distance in angstroms when supplied by ProLIF;
- finite angle or angle-deviation values in degrees when supplied by ProLIF;
- ligand/protein atom indices when supplied by ProLIF;
- `method="prolif"`, `quality="tool_derived"`, and analyzer version.

The normalizer will whitelist numeric geometry keys, validate their types and
finite ranges, and preserve no raw object representations. A record with a
valid interaction but no geometry remains an interaction identity observation
with `geometry_status="unavailable"`; it is never upgraded to a geometry
claim. If the analyzer cannot produce a trustworthy metadata structure, the
response remains explicit `unavailable` or `failed` with a machine-readable
reason code.

The existing no-topology and missing-dependency behavior remains unchanged.
The frontend and route contracts continue to receive the structured adapter
result; no fabricated distances, angles, contacts, or affinity values are
introduced.

## Error handling and provenance

- Missing receptor/ligand: `unavailable / analysis_input_missing`.
- Unsupported PDBQT-only topology: `unavailable / unsupported_topology`.
- Missing ProLIF or MDAnalysis: `unavailable / dependency_missing`.
- Missing metadata API or malformed metadata: `unavailable / geometry_metadata_unavailable`.
- Non-finite or invalid geometry values: omit the invalid field and add a
  warning; if no trustworthy geometry remains, set `geometry_status` to
  `unavailable`.
- Analyzer execution errors: `failed / analysis_execution_failed` without
  exposing absolute paths or raw exception text.

Provenance will include the analyzer name/version, metadata mode, and existing
input summaries. It will not include secrets or machine-specific absolute
paths.

## Testing and local acceptance

Tests are added before implementation and must demonstrate a failing
geometry-specific assertion before the adapter changes. The focused suite
will cover:

1. a valid metadata record with distance, angle, and atom indices;
2. multiple interaction occurrences and residue-pair normalization;
3. malformed, non-finite, negative, or oversized geometry values;
4. metadata API absence and dependency absence;
5. current PDBQT fail-closed behavior and existing ProLIF count behavior.

Local acceptance then runs the existing RDKit-enabled focused Python suites,
Agent contract acceptance, strict health check, JavaScript contract tests,
`node --check`, `compileall`, and the full Python test suite when the current
long-running real acceptance process has terminated. Results are recorded as
local evidence only; no target-server claim is made.

## Acceptance boundary

This design closes the locally implementable interaction-geometry evidence
gap and preserves honest failure states. It does not close the deployment
objective: real HTTPS/WSS, reverse proxy, production model endpoint, Temporal
restart recovery, target-server resource cleanup, and real reference-pose
redocking still require the target deployment environment.
