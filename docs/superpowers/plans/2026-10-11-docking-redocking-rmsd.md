# Docking explicit redocking RMSD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit-reference, independently-topologized redocking RMSD gate to docking scientific-quality reports and manifests.

**Architecture:** Keep RMSD policy in `src/docking/reproducibility.py`; recover the selected current Vina model from the complete output through Meeko in the docking service; pass only caller-supplied reference and threshold values through the structured adapter. Preserve missing-reference `not_run` behavior and retain failure reports before cleanup.

**Tech Stack:** Python 3.10+, RDKit, Meeko, AutoDock Vina PDBQT, pytest.

---

### Task 1: RMSD report contract

**Files:**
- Modify: `tests/test_docking_reproducibility.py`
- Modify: `src/docking/reproducibility.py`

- [x] **Step 1: Write failing tests** for pass, threshold failure without alignment, missing reference, invalid reference, missing threshold, and candidate unavailable.
- [x] **Step 2: Run `python -m pytest tests/test_docking_reproducibility.py -q` and verify the new keyword arguments/records fail.
- [x] **Step 3: Add keyword-only `reference_pose`, `candidate_pose`, and positive finite `redocking_rmsd_threshold` handling to `assess_pose_geometry()`; use `symmetry_aware_heavy_atom_rmsd()` and append a meaningful failure code when RMSD fails.
- [x] **Step 4: Re-run the focused tests and confirm all pass without changing binding-energy semantics.

### Task 2: Independent Vina candidate recovery

**Files:**
- Modify: `tests/test_docking_reproducibility.py`
- Modify: `src/docking/molecular_docking_service.py`

- [x] **Step 1: Add a failing Meeko round-trip test proving the selected `pose_index` maps to the corresponding conformer and rejects missing mapping/multiple ligands.
- [x] **Step 2: Run the focused test and verify the recovery helper is absent or fails closed.
- [x] **Step 3: Preserve full current Vina output, recover exactly one ligand with `PDBQTMolecule` and `RDKitMolCreate.from_pdbqt_mol`, select `results[0].pose_index - 1`, and reject malformed topology/export cases.
- [x] **Step 4: Run the focused test and existing parser/RMSD tests.

### Task 3: Service and adapter integration

**Files:**
- Modify: `tests/test_docking_reproducibility.py`
- Modify: `src/docking/molecular_docking_service.py`
- Modify: `src/docking/molecular_docking_adapter.py`

- [x] **Step 1: Add a failing integration-level test for caller-supplied reference/threshold propagation, manifest provenance, and failed-quality preservation across cleanup.
- [x] **Step 2: Wire optional validated reference path and threshold through `execute()` and `perform_docking()`; never search stale output/history and never expose an absolute reference path.
- [x] **Step 3: Persist successful and failed `scientific_quality` records plus bounded reference/output hashes and selected model/version provenance in the run manifest.
- [x] **Step 4: Run focused tests, `python -m compileall -q src scripts`, and the repository docking/agent quality checks available locally.

### Task 4: Review and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-10-11-docking-redocking-rmsd.md`

- [x] **Step 1:** Inspect the diff for unrelated changes, absolute paths, fabricated scientific values, and API compatibility regressions.
- [x] **Step 2:** Record the exact tests and the production acceptance boundary; leave HTTPS/WSS, active model weights, server restart, and real Vina redocking as pending when assets are unavailable.
- [x] **Step 3:** Commit only the task files with a Conventional Commit message.
