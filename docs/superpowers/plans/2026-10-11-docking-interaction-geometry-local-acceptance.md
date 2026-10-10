# Docking Interaction Geometry Local Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve trustworthy ProLIF interaction geometry when the optional analyzer provides it, while keeping missing or malformed scientific evidence explicitly unavailable and documenting the local-only acceptance boundary.

**Architecture:** Keep `src/docking/interaction_analysis.py` as the single boundary between ProLIF's version-dependent output and the stable API DTO. Add a small, pure normalizer for metadata records that whitelists finite geometry and atom-index fields, and let the existing count normalizer continue to provide compatibility for count-only data. The runtime path will request/consume metadata when supported, but will not infer geometry from coordinates or PDBQT files.

**Tech Stack:** Python 3.10+, ProLIF/MDAnalysis when installed, existing FastAPI docking adapter DTOs, pytest, RDKit-enabled local environment.

---

### Task 1: Add failing tests for geometry metadata normalization

**Files:**
- Modify: `tests/test_docking_interaction_analysis.py`
- Test: `tests/test_docking_interaction_analysis.py`

- [ ] **Step 1: Write the failing tests**

Add tests that describe the public DTO without coupling to a ProLIF internal class:

```python
def test_prolif_metadata_preserves_tool_derived_geometry_and_atom_indices():
    result = normalize_prolif_output(
        {
            ("ASP:123", "LIG:1"): {
                "HBDonor": [
                    {
                        "distance": 2.91,
                        "angle": 157.4,
                        "protein_indices": [4],
                        "ligand_indices": [0, 1],
                    }
                ]
            }
        }
    )

    assert result["geometry_status"] == "available"
    assert result["interactions"] == [
        {
            "type": "hydrogen_bond",
            "protein_residue": "ASP:123",
            "ligand_residue": "LIG:1",
            "count": 1,
            "distance_angstrom": 2.91,
            "angle_degrees": 157.4,
            "protein_atom_indices": [4],
            "ligand_atom_indices": [0, 1],
            "method": "prolif",
            "quality": "tool_derived",
        }
    ]


def test_invalid_geometry_is_omitted_and_reported_as_unavailable():
    result = normalize_prolif_output(
        {
            ("ASP:123", "LIG:1"): {
                "HBDonor": [
                    {
                        "distance": float("nan"),
                        "angle": -5,
                        "protein_indices": ["bad"],
                    }
                ]
            }
        }
    )

    assert result["geometry_status"] == "unavailable"
    assert "invalid_geometry_metadata" in result["warnings"]
    assert "distance_angstrom" not in result["interactions"][0]
    assert "angle_degrees" not in result["interactions"][0]
    assert "protein_atom_indices" not in result["interactions"][0]


def test_count_only_output_remains_compatible_but_geometry_is_unavailable():
    result = normalize_prolif_output(
        {("ASP:123", "LIG:1"): {"HBDonor": 2}}
    )

    assert result["geometry_status"] == "unavailable"
    assert result["interactions"][0]["count"] == 2
    assert "distance_angstrom" not in result["interactions"][0]


def test_metadata_api_absence_returns_explicit_unavailable_result(tmp_path, monkeypatch):
    receptor = tmp_path / "receptor.pdb"
    ligand = tmp_path / "pose.sdf"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("sdf\n", encoding="utf-8")

    class FakeFingerprint:
        def __init__(self, **_kwargs):
            pass

        def run_from_iterable(self, _poses, _protein):
            pass

        def to_dataframe(self):
            return SimpleNamespace(index=[0], iloc=[SimpleNamespace(to_dict=lambda: {})])

    fake_mda = SimpleNamespace(
        Universe=lambda _path: SimpleNamespace(atoms=[SimpleNamespace(element="H", name="H1")])
    )
    fake_prolif = SimpleNamespace(
        __version__="test",
        Molecule=SimpleNamespace(from_mda=lambda _universe: object()),
        sdf_supplier=lambda _path: [object()],
        Fingerprint=FakeFingerprint,
    )
    monkeypatch.setitem(sys.modules, "MDAnalysis", fake_mda)
    monkeypatch.setitem(sys.modules, "prolif", fake_prolif)
    monkeypatch.setattr("src.docking.interaction_analysis._dependency_available", lambda _: True)

    result = analyze_docking_interactions(receptor, ligand)

    assert result["status"] == "unavailable"
    assert result["reason_code"] == "geometry_metadata_unavailable"
    assert result["geometry_status"] == "unavailable"
```

The existing count-only, missing dependency, PDBQT, and fake ProLIF tests remain in place and must continue to pass.

- [ ] **Step 2: Run the new tests to verify RED**

Run:

```powershell
python -m pytest tests/test_docking_interaction_analysis.py -q
```

Expected: the new geometry assertions fail because the current normalizer has no `geometry_status` or geometry fields, and the metadata API absence case is currently reported as a successful count-only analysis.

- [ ] **Step 3: Commit the failing-test change**

```powershell
git add tests/test_docking_interaction_analysis.py
git commit -m "test: specify docking interaction geometry metadata"
```

### Task 2: Implement safe ProLIF metadata normalization

**Files:**
- Modify: `src/docking/interaction_analysis.py`
- Test: `tests/test_docking_interaction_analysis.py`

- [ ] **Step 1: Add explicit geometry helpers**

Implement pure helpers in `src/docking/interaction_analysis.py`:

```python
import math


def _finite_positive_number(value: Any, *, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > maximum:
        return None
    return number


def _finite_angle(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 360:
        return None
    return number


def _atom_indices(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value):
        return None
    return list(value)
```

Use bounded values (`distance <= 20 Å`, `angle <= 360°`) only as input validation; do not transform or estimate values. Treat a malformed field as absent and add one warning per normalization result.

- [ ] **Step 2: Extend the normalizer with metadata occurrences**

Update `normalize_prolif_output` to return `geometry_status` (`available` only when at least one valid geometry field or valid atom-index mapping is preserved; otherwise `unavailable`) and to accept each interaction value as either:

1. a numeric count, preserving current behavior; or
2. a sequence of occurrence mappings, one DTO interaction per occurrence, with `count` equal to the occurrence count.

Whitelist these source keys and map them to stable DTO keys:

```python
"distance" -> "distance_angstrom"
"distance_angstrom" -> "distance_angstrom"
"angle" -> "angle_degrees"
"angle_degrees" -> "angle_degrees"
"protein_indices" -> "protein_atom_indices"
"ligand_indices" -> "ligand_atom_indices"
```

Do not copy unknown keys or raw ProLIF objects. Preserve `method="prolif"` and `quality="tool_derived"`. If an occurrence has no valid geometry, still preserve its interaction identity/count while leaving geometry fields out.

- [ ] **Step 3: Run the focused tests to verify GREEN**

Run:

```powershell
python -m pytest tests/test_docking_interaction_analysis.py -q
```

Expected: all focused tests pass. If an assertion fails, adjust only the implementation or the test's explicitly defined DTO contract; do not loosen validation to accept malformed values.

- [ ] **Step 4: Commit the normalizer**

```powershell
git add src/docking/interaction_analysis.py tests/test_docking_interaction_analysis.py
git commit -m "feat: preserve validated docking interaction geometry"
```

### Task 3: Wire metadata-capable ProLIF execution and fail closed

**Files:**
- Modify: `src/docking/interaction_analysis.py`
- Test: `tests/test_docking_interaction_analysis.py`

- [ ] **Step 1: Add a fake metadata-capable fingerprint test**

Extend the fake ProLIF test so `Fingerprint` records whether `metadata=True` was requested and exposes a metadata-bearing `ifp` structure. Assert the returned result contains the geometry DTO, `geometry_status="available"`, `provenance["metadata_mode"]="metadata"`, and the ProLIF version. Keep one separate fake that has no metadata/`ifp` capability and assert `status="unavailable"`, `reason_code="geometry_metadata_unavailable"`; this prevents a count dataframe from being misrepresented as geometry evidence.

- [ ] **Step 2: Run the new execution test to verify RED**

Run:

```powershell
python -m pytest tests/test_docking_interaction_analysis.py -q -k metadata
```

Expected: the metadata request/provenance assertions fail against the current `Fingerprint(count=True)` and count-only dataframe path.

- [ ] **Step 3: Implement the smallest compatible metadata path**

Construct the fingerprint with `count=True, metadata=True` when that keyword is accepted. If the installed ProLIF raises `TypeError` for the keyword, retry with `count=True`, then inspect whether a documented metadata-bearing `ifp` is present. Prefer `fingerprint.ifp` for geometry; use `to_dataframe()` only for identity/count compatibility. If no metadata structure can be obtained, return:

```python
_result(
    "unavailable",
    "geometry_metadata_unavailable",
    warnings=["prolif_metadata_is_required_for_geometry_claims"],
    provenance={
        "analyzer": "prolif",
        "tool_derived": False,
        "metadata_mode": "unavailable",
        "prolif_version": version,
    },
)
```

When metadata exists, pass it through `normalize_prolif_output`, attach `metadata_mode="metadata"` and existing input hashes to provenance, and return `success` only for an analyzer-completed result. A count-only ProLIF dataframe remains supported only where the existing fake/legacy identity contract requires it; it must state `geometry_status="unavailable"` and never include geometry fields.

- [ ] **Step 4: Run the focused suite to verify GREEN**

Run:

```powershell
python -m pytest tests/test_docking_interaction_analysis.py -q
```

Expected: all focused tests pass, including existing PDBQT fail-closed and count compatibility tests.

- [ ] **Step 5: Commit the runtime path**

```powershell
git add src/docking/interaction_analysis.py tests/test_docking_interaction_analysis.py
git commit -m "feat: gate docking geometry on prolif metadata"
```

### Task 4: Run local acceptance and record the boundary

**Files:**
- Modify: `docs/handoff/latest.md`
- Test: existing repository suites and scripts

- [ ] **Step 1: Run focused Python acceptance**

Run:

```powershell
python -m pytest tests/test_docking_interaction_analysis.py tests/test_docking_reproducibility.py tests/agent -q
```

Expected: all runnable tests pass; optional ProLIF/MDAnalysis tests may be skipped only when those dependencies are absent.

- [ ] **Step 2: Run repository static and health checks**

Run:

```powershell
python -m compileall -q src scripts
python scripts/health_check.py --strict
node tests/home_agent_task_panel_test.js
node tests/reverse_target_broad_recall_test.js
node tests/reverse_target_pagination_test.js
Get-ChildItem src/web/static/js -Recurse -Filter *.js -File |
  Where-Object { $_.FullName -notmatch 'ketcher|backup' } |
  ForEach-Object { node --check $_.FullName }
```

Expected: compile and health checks pass; existing JavaScript contract tests and syntax checks pass.

- [ ] **Step 3: Run the full local Python suite**

Run:

```powershell
python -m pytest tests -q
```

Expected: exit code 0. Record exact pass/skip/warning counts from the completed process; do not report a running or interrupted process as acceptance evidence.

- [ ] **Step 4: Record local-only evidence**

Update `docs/handoff/latest.md` with the exact commands and observed result categories. State that missing ProLIF/MDAnalysis or unavailable PDE/BuChE/external services remain unavailable, and that target HTTPS/WSS, reverse proxy, Temporal/restart/multi-worker, production model assets, and real reference-pose redocking remain unverified. Do not add absolute workstation paths, user input, secrets, simulated scores, or fabricated geometry.

- [ ] **Step 5: Review and commit the acceptance evidence**

Run:

```powershell
git diff --check
git status --short
```

Confirm only the planned source, tests, and handoff files are changed; preserve the pre-existing untracked user files. Then commit:

```powershell
git add src/docking/interaction_analysis.py tests/test_docking_interaction_analysis.py docs/handoff/latest.md
git commit -m "docs: record local docking geometry acceptance"
```

## Plan Self-Review

- The design's geometry, fail-closed, provenance, compatibility, and local-acceptance requirements are covered by Tasks 1–4.
- No mandatory dependency change or deployment claim is included.
- All geometry fields are explicitly whitelisted and validated before serialization.
- The existing count-only and PDBQT behavior remains covered by regression tests.
- The pre-existing untracked user files are not included in any task or commit.
