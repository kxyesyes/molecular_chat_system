# Docking explicit redocking RMSD design

## Goal

Make redocking RMSD an honest, reproducible part of the docking scientific-quality report when, and only when, an explicit reference pose is supplied. The change must not turn a missing reference pose into a fabricated pass or infer a reference from an output artifact that may be stale.

## Scope and non-goals

In scope:

- extend the existing pose-quality assessment with an optional reference pose and an explicit RMSD threshold;
- calculate symmetry-aware heavy-atom RMSD in the receptor coordinate frame using the existing `symmetry_aware_heavy_atom_rmsd` helper;
- report `passed`, `failed`, or `not_run` with machine-readable reason codes and measured RMSD when available;
- wire the normal docking flow only to an explicitly validated reference artifact supplied by the caller;
- add regression tests for pass, threshold failure, missing reference, invalid reference, and coordinate-frame behavior;
- record the result in the existing `scientific_quality` run manifest without changing binding-energy semantics.

Out of scope:

- selecting a reference pose from the Vina output or history index;
- inventing a universal scientific threshold when the caller has not supplied one;
- asserting binding affinity, interaction geometry, or experimental validity from RMSD alone;
- requiring ProLIF/MDAnalysis or changing the interaction-analysis adapter;
- shipping reference structures, model weights, or other runtime assets.

## API and data flow

`assess_pose_geometry()` receives three optional keyword-only values:

- `reference_pose`: an RDKit molecule or supported structure-file path containing exactly one valid 3-D conformer;
- `candidate_pose`: a topology-bearing RDKit molecule independently exported from the selected current Vina output model, never constructed by copying reference topology or assuming atom order;
- `redocking_rmsd_threshold`: a finite positive numeric threshold in angstroms.

The report always contains the existing `redocking_rmsd` object. Its contract is:

- `status=not_run`, `reason_code=reference_pose_not_supplied` when no reference is supplied;
- `status=not_run`, `reason_code=threshold_not_supplied` when a reference exists but no explicit policy threshold exists;
- `status=failed`, `reason_code=candidate_pose_unavailable` when requested validation has no independently recovered candidate topology;
- `status=failed`, `reason_code=reference_pose_invalid` when reference loading or RMSD calculation rejects the structure;
- `status=passed` or `failed`, `reason_code=rmsd_within_threshold` or `rmsd_exceeds_threshold`, with numeric `value_angstrom` and `threshold_angstrom`, when both inputs are valid.

The overall pose-quality status remains governed by existing coordinate, box, and atom-count gates plus the RMSD result when it is evaluated. A `not_run` RMSD does not fail ordinary development docking, but production acceptance can require a non-`not_run` result through its existing external evidence policy.

The main docking service passes these values only when the caller has supplied an explicit reference pose and threshold through the existing request/configuration boundary. It never searches the output directory, history, or stale artifacts for a reference. Existing callers that do not provide them preserve the current `not_run` behavior.

## Candidate topology and model selection

The service must export the best-energy model from the full current Vina PDBQT output using Meeko's `PDBQTMolecule` and `RDKitMolCreate.from_pdbqt_mol` APIs. The full output is needed because `DockingResult.pose_data` currently retains only atom lines, omitting the SMILES/index mapping remarks. Meeko's result list represents ligands, not individual poses; the selected model corresponds to a conformer within the single ligand molecule. Select `results[0].pose_index - 1`, not the first conformer by assumption. Reject missing mappings, multiple ligands, missing conformers, or export failure. Do not guess bonds from coordinates or copy chemical identity from the reference to make comparison possible.

Recover topology independently of the reference, verify chemical identity using the existing RMSD helper, and compare only heavy atoms. Retain the explicit reference snapshot and its hash, selected model index, current output hash, and Meeko/RDKit versions for provenance. The public report must not disclose absolute reference paths. Failures must survive in the returned scientific-quality report even if normal cleanup removes failed job artifacts. Successful reports must also be persisted in the run manifest; returning a report alone is insufficient.

Sources: [Meeko export documentation](https://github.com/forlilab/Meeko/blob/develop/docs/source/export_usage.rst) and the installed `meeko.rdkit_mol_create` implementation. Unit-test fixtures using Meeko serialization are synthetic fixtures, not evidence of an actual Vina run.

## Validation and failure handling

- Reference structures use the existing loader and 3-D/conformer/chemical-identity checks.
- RMSD is calculated without fitting away translation or rotation, because both poses must already be in the receptor frame.
- Symmetry mapping remains bounded by the existing helper to avoid pathological work.
- Non-finite values, multiple conformers, missing coordinates, different heavy-atom counts, or different chemical identity produce an explicit failed RMSD record and an overall scientific-quality failure.
- No binding energy or interaction record is changed by this feature.

## Tests

Add tests before implementation for:

1. an explicit equivalent reference and candidate below threshold produce a passed RMSD record;
2. a translated candidate produces a measured RMSD above threshold and fails without alignment;
3. missing reference remains `not_run` with the existing reason code;
4. a reference without a usable 3-D conformer fails closed;
5. a reference supplied without a threshold remains `not_run` rather than using an invented default;
6. existing pose geometry and seed-stability tests remain unchanged and green.

The focused Python suite must run in the MedChat Conda environment, followed by `compileall` and the repository quality checks. No external model, server, or credential is needed for this slice.

## Acceptance boundary

This slice proves local scientific-quality bookkeeping and RMSD semantics only. It does not prove target-server HTTPS/WSS, Temporal restart recovery, active PDE/BuChE/RG-MPNN models, or real Vina redocking because those require target deployment assets and services. Those gates remain explicitly pending in production acceptance evidence.
