# Auditable Receptor Conformer Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a separately identified, fully traceable single-conformer receptor input from an authenticated original structure and explicit scientific selections, without altering the original or weakening the existing strict D1 entry point.

**Architecture:** Separate bounded original-PDB parsing, independently supplied source inventory validation, and deterministic selection/materialization. Run the pure producers through the existing retained preparation process owner. Bind their outputs into D2–D6 of the approved chat-docking plan before enabling any execution pathway.

**Tech Stack:** Python 3.10+, existing FastAPI/Jinja/native JavaScript and SQLite ownership/consent infrastructure, pytest and the existing Node scripts. No new scientific repair library, service, scheduler or model is introduced.

---

## Authorization, dependencies and release order

Approved design: `docs/superpowers/specs/2026-10-16-receptor-conformer-projection-design.md`.
The maintainer confirmed option A on 2026-10-16, then authorized finishing the remaining
work and submitting, pushing and merging after verification. Reuse
`codex/browser-agent-real-acceptance` and PR #209; planning baseline is `21b0b79c`.
Do not create another PR or disturb unrelated untracked work.
The later release authorization supersedes the earlier no-merge delivery restriction;
it does not supersede any scientific verification or application ownership gate.

This supplements, not replaces, `2026-10-15-chat-docking-closure.md`. P1–P5 below
are independently testable pure/worker components. P6 is an explicit integration
gate within that plan's D2–D6. P7 closes the real S10 gate. R3's production logging
initialization is reviewed and committed; its test-host follow-up and R4's ranked
chat binding remain separate required work.
No open review finding is accepted merely because another suite passes.

General development/release authorization does not fabricate a runtime consent
record, change a scientific choice silently, or replace the current bound Vina
approval interaction. The source/selection/file/rule identities must be fixed and
the existing current-card approval contract satisfied before scientific execution.

## Exact file map

| File | Responsibility |
|---|---|
| `src/docking/receptor_projection_pdb.py` | Bounded original-coordinate and connection parsing; no selection, network or authentication |
| `src/docking/receptor_projection_source.py` | Closed source/selection schemas, resource preflight, inventory and completeness comparisons |
| `src/docking/receptor_projection.py` | Public inspection, projection, round-trip verification and versioned provenance |
| `src/docking/chat_preparation.py` | Three explicit new operation dispatches through the existing owner; old operations unchanged |
| `tests/test_receptor_projection_pdb.py` | Byte-format, models, altloc, occupancy, connection and allocation negatives |
| `tests/test_receptor_projection_source.py` | Native types, closed schemas, inventory, chemical-component and selection negatives |
| `tests/test_receptor_projection.py` | Scientific choices, deterministic output, lineage, pocket geometry and tamper tests |
| `tests/test_chat_docking_preparation.py` | Real child lifecycle and operation-boundary tests |
| `src/web/docking_request.py` / `tests/agent/test_web_docking_consent.py` | D2-owned authentication, immutable draft/source binding and projection integration |
| D3–D6 files already listed in the chat-docking plan | Bound preview/approval, invalidation, isolation and existing homepage controls |

Do not modify `src/docking/pocket_evidence.py` to accept new input types. Its
`docking-source-v1` callers must retain all existing rejection behavior.

## Shared contracts established before implementation

All public inputs are exact native types, not coercible subclasses. Lists have
unique identities; dictionaries are closed. JSON canonicalization uses sorted
keys, compact separators, UTF-8 and `allow_nan=False`, only after bounded traversal.
No source string is interpreted as a filesystem path, URL, approval or Python code.

### Source descriptor: `receptor-source-v1`

The exact root keys are `schema_version`, `sha256`, `pdb_id`, `provenance`, `target`,
`proteins`, `residues`, `connections`, `conformer_constraints`, `limitations`.
`sha256` binds the original bytes. PDB ID, target, protein identity and provenance
fields use the same validation rules as D1. This different schema is never sent to
the old strict producer.

Each residue has exactly `identity`, `record`, `entity_id`, `role`, `component`,
`atoms`, `required_heavy_atoms`. `identity` remains
`{model, chain, resname, resseq, icode}`. `component` is
`{record_id, version, sha256}` for the independently verified chemical definition.
`required_heavy_atoms` is a unique list of `{name, element}` for that residue's
actual deposited chemical context, including independently established termini
and modifications. It must not be reconstructed from the observed PDB atoms.

Each atom has exactly `serial`, `name`, `element`, `altloc`,
`occupancy_hundredths`, `xyz_milli`, `bfactor_hundredths`, `charge`.
`altloc` is empty or one ASCII alphanumeric character; occupancy is an integer
0–100, xyz is exactly three native integers matching the deposited 0.001 Å fields,
temperature factor is a nonnegative native integer matching the original 0.01
field, and charge is empty or the supported PDB two-character charge. Hydrogen
and water are included in the COMPLETE inventory. Original fixed-width text stays
available separately; scaled integers do not authorize rewriting its spelling.

Each connection is exactly `{record_index, record_type, atom_serials, kind,
symmetry}`. The original physical line index is one-based; record_type is
`LINK`, `SSBOND` or `CONECT`; atom_serials preserves the normalized endpoint/order
and multiplicity represented by that record. `kind` is `covalent`, `disulfide`,
`coordination` or `unknown`, established by the independent source. `symmetry`
contains the exact endpoint operator tokens normalized to identity or nonidentity.
No distance is used to invent connection chemistry. Unsupported or ambiguous
endpoint/symmetry semantics cannot produce a projected input.

Each conformer constraint is exactly `{id, residues, allowed_assignments}`.
Each allowed assignment is an ordered list of actual altloc labels, one per listed
residue. Validate all identities/labels and bounded combinations; no enumerating
the Cartesian product. Missing information for a recorded cross-residue constraint
is an unavailable source, not an empty constraint. Empty constraints do not claim
that the entire receptor is energetically validated; they mean no additional
recorded dependency was established in the authenticated source set.

Each limitation is exactly `{id, kind, residues, source_record_id}`. Supported
kinds are `missing_residues`, `construct_truncation`, `sequence_variant`.
Only these reviewable limitations can be acknowledged. Missing required atoms,
zero-occupancy retained protein atoms and unsupported chemistry are hard failures,
not limitations with an override checkbox.

Authenticity remains a D2 responsibility: independently verified, same-version
official atom/connection/component evidence, plus owner/root/current-source binding.
These pure functions cannot distinguish a forged self-consistent dictionary from
trusted source facts; every successful pure result says
`source_authentication="caller_responsibility"`. P6 must reject such forgery at
the actual authority boundary before invoking or publishing the producer.

### Explicit selection

The exact keys are `protein_selection`, `reference_residue`, `hetero_choices`,
`conformer_choices`, `reviewed_limitations`.

- `protein_selection` retains D1's `{model, chains:[{chain, entity_id}]}` shape;
  every selected protein matches the authenticated target and species.
- `reference_residue` is one complete residue identity, not a ligand name search.
- `hetero_choices` contains `{residue, action}` for every nonprotein residue other
  than the reference; action is `retain` or `remove`. No hidden default for water,
  glycans, metal, cofactors or solvent. Modified protein is never classified as
  removable hetero merely because its record is HETATM.
- `conformer_choices` contains `{residue, altloc}` exactly for retained/reference
  residues with nonblank alternatives. No omitted or extraneous choice; blank
  common atoms are combined only with the single selected complete conformer.
- `reviewed_limitations` is the exact unique set of applicable limitation IDs for
  selected chains. Acknowledgment cannot clear a hard rejection.

Normalize set-like selection lists to original source residue/chain order and
limitation IDs to lexical order before hashing. Duplicates still reject; different
input list ordering must not silently change the physical selection or digest.

### Public entry points and output identity

```python
def inspect_receptor_projection(pdb_bytes: bytes, *, source: dict) -> dict:
    """Bounded source-bound choices and restrictions, never execution readiness."""

def project_receptor_bytes(pdb_bytes: bytes, *, source: dict, selection: dict) -> dict:
    """Validated independent derivative, pocket proposal and complete lineage."""

def verify_receptor_projection(pdb_bytes: bytes, *, source: dict, selection: dict,
                               derived_bytes: bytes, projection: dict) -> dict:
    """Recompute and compare the full derivative/descriptor; no hash-only trust."""
```

Inspection returns `ok`, `source_authentication`, `source_sha256`, `choices`,
`limitations`, `input_ready=False`; it never returns receptor bytes. Projection
success returns exactly `ok`, `source_authentication`, `pdb_bytes`, `projection`,
`pocket`. Verification success returns only `ok`, `source_authentication` and
`projection_sha256`. Errors are closed safe objects with `ok=False`,
`code="RECEPTOR_PROJECTION_INVALID"`, a fixed reason enum and fixed message;
no partial bytes, caller text, paths, exception strings, score or grant.

`projection` has exactly `schema_version="receptor-projection-v1"`,
`rule_version="explicit-residue-conformer-v1"`, `original_source`, `selection`,
`derived_input`, `lineage`, `limitations`. Original-source facts contain original
byte/descriptor hashes and unchanged target/provenance identity. Selection stores
the canonical validated choice plus its digest. Derived-input facts contain its
hash, byte/atom counts and fixed metadata policy. Lineage has one row per original
atom: `{source_serial, derived_serial, altloc, occupancy_hundredths,
source_line_sha256, disposition, reason}`. Removed atoms have null derived_serial.
Reasons are fixed: `retained`, `unselected_conformer`, `unselected_protein`,
`reference_removed`, `explicit_hetero_removal`. Every raw atom occurs exactly once.

### Resource policy

Original/derived PDB <=16 MiB; original atoms <=99,999; atom plus emitted TER serials
must fit 99,999; one model; <=62 selected protein chains. Source/selection traversal
depth <=12, <=4,000,000 nodes, each scalar string <=128 except no free-form strings
are allowed. Source canonical JSON <=64 MiB; selection <=8 MiB; projection/lineage
canonical JSON <=64 MiB; <=256 constraint groups, <=256 assignments per group and
<=64 residues per group. Each list's tighter semantic limit is checked before
copying or extending scratch arrays. Zero-size inventories reject.

Use an explicit logical allocation budget <=512 MiB for retained parse indexes,
normalization/copy scratch and serialization capacity. Charge before allocation;
reservations include escaped text and concurrent byte/Unicode copies, not merely
final `nbytes`. These ceilings may reject large valid structures and are not OS
RSS guarantees. Do not grow these limits or retry to obtain an acceptance result.
Child cap stays 60 seconds and the original upstream deadline stays 180 seconds.

## P1 — Bounded original PDB parser, with no filtering

**Files:** create receptor_projection_pdb.py and test_receptor_projection_pdb.py.

- [x] Add call-phase missing-module RED, then a fixed-width synthetic fixture using
  `atom_line` and `residue` from `tests/test_docking_pocket_evidence.py`. Use complete
  ALA heavy atoms N/CA/C/O plus CB alternatives A/B (occupancies 0.60/0.40), one
  three-heavy-atom synthetic reference and water with occupancy zero. State clearly
  that this fixture is a format/schema test, not experimental or docking evidence.

```python
def test_projection_parser_exists():
    import importlib.util
    assert importlib.util.find_spec("src.docking.receptor_projection_pdb") is not None

def test_raw_parser_preserves_all_alternatives_and_zero_water(raw_fixture):
    from src.docking.receptor_projection_pdb import parse_original_pdb
    parsed = parse_original_pdb(raw_fixture)
    assert len(parsed.atoms) == 10
    assert [(a.altloc, a.occupancy_hundredths) for a in parsed.atoms[4:6]] == [("A", 60), ("B", 40)]
    assert parsed.atoms[-1].occupancy_hundredths == 0
    assert parsed.original_bytes == raw_fixture
```

- [x] Run `python -m pytest tests/test_receptor_projection_pdb.py -q`; verify a
  missing callable/module assertion, not collection failure. Record the result.
- [x] Implement `parse_original_pdb(pdb_bytes) -> ParsedPdb` with immutable internal
  parsed atoms/records. Reuse D1's supported field syntax and envelope rules, but
  independently parse altloc and occupancy into the new types. No call to old
  `_parse` after stripping columns. Do not mutate any input line.

  Define `ProjectionInvalid(ValueError)` with an allowlisted reason only,
  `ParsedAtom` with serial/name/element/identity/record/altloc/scaled fields plus
  original line bytes and physical line index, `ParsedConnection` with original
  record identity and all resolved endpoint facts, and `ParsedPdb` with original
  bytes/header/model/ordered atoms/ordered records/connections. These are internal
  frozen dataclasses, not client-provided authority objects. Define
  `parse_scaled_decimal(field, places)` to reject exponent, whitespace inside
  numbers and non-decimal spellings before exact base-10 integer conversion.

```python
# Exact changed coordinate-field rule within the new parser, not the old parser:
altloc = line[16].strip()
if altloc and (not altloc.isascii() or not altloc.isalnum()):
    raise ProjectionInvalid("raw_invalid")
occupancy_hundredths = parse_scaled_decimal(line[54:60], places=2)
if not 0 <= occupancy_hundredths <= 100:
    raise ProjectionInvalid("raw_invalid")
# parse_scaled_decimal validates the fixed decimal grammar before integer scaling.
# Same-residue uniqueness is now (atom name, altloc), never name alone.
```

- [x] Add RED cases for non-ASCII/control/oversize/truncated records, duplicate
  atom serials or name+altloc, conflicting resnames at one sequence position,
  malformed/NaN/infinite occupancy and coordinates, negative/>1 occupancy,
  unknown records, malformed TER/END/MODEL and mixed models. Include invalid
  unselected altloc, water and non-target chains: all must still fail inspection.
- [x] Parse LINK atom/altloc/residue endpoints and symmetry, SSBOND residue endpoints
  and symmetry, and ordered CONECT serial fields. After all atoms are read, resolve
  every endpoint or record ambiguity explicitly; dangling references reject.
  Nonidentity symmetry is unsupported—never synthesize crystal mates.
- [x] GREEN parser tests plus existing D1 strict tests; independent spec then quality
  review. Commit only the two files and this task's measured progress.

P1 measured record (2026-10-16): initial missing-module **82 failed**, connectivity
**46 failed**, preallocation **7 failed**, and self-review boundary **3 failed**
were followed by green batches, ending at **193 P1 + 58 D1 = 251 passed**.
Independent SPEC found two P2 issues: common/alternate same-name ambiguity and
self/incompatible-alternate connections. Added tests reproduced **2** and **10**
failures; fixes produced **217 P1 + 58 D1 = 275 passed**. SPEC rereview passed,
including rejection of all seven original independent negative probes.
Fresh QUALITY APPROVED with **275 passed**, 7,999 synthetic byte/truncation probes,
and actual atom/byte-limit checks. No old D1 acceptance rule changed. Logical
preflight accounting is not an OS memory-isolation claim.

Read-only official-file syntax checks also passed for 6SAM (4,521 atoms / 245
connection records), 6T9S (4,438 / 196), 6XTA (4,480 / 213), and 7AMZ (4,637 / 288).
3O9M rejected as `raw_invalid`. These checks downloaded bounded original bytes
into memory only; they establish neither authenticated source inventory, complete
chemistry, eligible pocket, prepared receptor nor real docking acceptance.

## P2 — Complete source inventory and bounded contracts

**Files:** create receptor_projection_source.py and test_receptor_projection_source.py;
extend the reusable synthetic fixture in the new test modules only.

- [x] Write RED for `validate_projection_source(parsed, source)` returning a detached
  validated internal source; exact parsed atom/connection multiset must match the
  independent descriptor, including water, hydrogen, raw occupancy and coordinates.
  The fixture descriptor is authored separately, not generated by the parser under test.

```python
def test_source_cannot_omit_unselected_atom(full_fixture):
    import copy
    from src.docking.receptor_projection_pdb import parse_original_pdb, ProjectionInvalid
    from src.docking.receptor_projection_source import validate_projection_source
    pdb, source, selection = full_fixture
    bad = copy.deepcopy(source)
    bad["residues"][-1]["atoms"] = []
    with pytest.raises(ProjectionInvalid, match="source_invalid"):
        validate_projection_source(parse_original_pdb(pdb), bad)
```

- [x] Run the new source test module RED. Implement `check_projection_resources`
  as an iterative exact-native traversal before copies, hash serialization or IPC.
  Implement source validation using the contract above; return frozen/detached
  facts and preserve the explicit caller-authentication boundary.
- [x] Add RED/GREEN mutations for boolean integers, subclasses, extra keys, cycles,
  depth/nodes/large lists/long scalars, source hash/version/PDB mismatch, wrong
  species/entity/chain, missing/extra/renamed atoms, altered elements/xyz/occupancy,
  incomplete connection/chemical metadata and aliased caller mutation.
- [x] Count each raw/source atom once; compare indexed tuples rather than repeated
  quadratic list searches. Required-heavy definitions are separately validated,
  including nonstandard protein identity; HETATM cannot authorize deleting protein.
- [x] Test allocation gates with instrumented reservation/serialization boundaries:
  no giant result is built and then rejected. A lowered test budget must reject
  before the guarded allocation; default bounds are not modified to pass tests.
- [x] GREEN source/parser/strict-D1 modules; two-stage review then scoped commit.

P2 measured record (2026-10-16): call-phase missing-module RED, followed by an
additional connection/constraint batch with **11 failed** before implementation.
Final worker and independent SPEC runs each obtained **190 P2 + 275 P1/D1 = 465
passed**. SPEC also passed 118 adverse probes and 500 independent canonical-size/
node-count checks. Fresh independent QUALITY APPROVED with **465 passed** and
1,421 additional probes. A 12,000-atom synthetic input passed; 36,000 atoms hit
the aggregate logical resource ceiling before descriptor serialization.

The only P1 change adds the fixed `source_invalid` reason. No old D1 rule changed.
Complete atom/connection facts and independent chemical definitions are detached
and frozen, but remain explicitly `source_authentication=caller_responsibility`.
No source authentication, conformer selection, derivative, ready input or browser
docking result is established by this pure validator. P3–P7 remain required.

## P3 — Explicit conformer, component and scientific selection

**Files:** create receptor_projection.py and test_receptor_projection.py;
extend source validation for the exact selection contract.

- [x] Write RED inspection returning complete source-bound choices and no bytes;
  a missing conformer or hetero choice cannot pass selection validation.

```python
def test_no_implicit_altloc_choice(full_fixture):
    import pytest
    from src.docking.receptor_projection_pdb import parse_original_pdb, ProjectionInvalid
    from src.docking.receptor_projection_source import validate_projection_source, validate_projection_selection
    from tests.test_receptor_projection import selection as make_selection
    pdb_bytes, source = full_fixture
    selection = make_selection()
    selection["conformer_choices"] = []
    parsed = parse_original_pdb(pdb_bytes)
    validated = validate_projection_source(parsed, source)
    with pytest.raises(ProjectionInvalid, match="selection_incomplete"):
        validate_projection_selection(parsed, validated, selection)
```

- [x] Implement `inspect_receptor_projection` and
  `validate_projection_selection(parsed, validated_source, selection)`.
  Selection traverses residues in original order. For each retained/reference
  residue, use only common blank atoms plus one explicitly chosen actual label:

```python
chosen = [atom for atom in residue_atoms if atom.altloc in ("", selected_altloc)]
names = [(atom.name, atom.element) for atom in chosen]
if len({name for name, _ in names}) != len(names):
    raise ProjectionInvalid("selection_invalid")
heavy = {(atom.name, atom.element) for atom in chosen if atom.element not in {"H", "D"}}
if heavy != required_heavy_atoms:
    raise ProjectionInvalid("incomplete_heavy_atoms")
```

- [x] Add one RED/GREEN batch per rule: missing/extra/duplicate choices; choosing
  highest occupancy per atom; shared/alternate same-name collision; incomplete
  selected conformer; wrong target/chain/entity; missing required heavy atoms;
  retained protein or other retained atom at occupancy zero; complete reference
  heavy atoms not all 100; unverified/glycan/metal/buffer reference; mutated input
  after validation; missing/extra limitation acknowledgments. No override booleans.
- [x] Validate every applicable cross-residue constraint against its supplied
  allowed assignments. Do not assume identical letters across residues prove a
  global conformer. An unresolved recorded constraint returns unavailable.
- [x] For all connections, selecting exactly one endpoint is invalid unless the
  record represents a verified unselected alternative and the complete chosen
  alternative relation is preserved. A reference linked to retained protein or
  required component is unsupported; never detach it to fabricate a normal
  noncovalent receptor. Ambiguous SSBOND alternate SG resolution also rejects.
  Specifically, both-retained edges are emitted and both-removed edges are
  recorded as excluded. A one-removed edge may be excluded only when its removed
  endpoint has reason `unselected_conformer`, the chosen same-residue/name/element
  counterpart is unique, and an actual independently verified edge of identical
  kind/symmetry to the retained endpoint already exists. Never create a replacement
  bond based only on matching names or geometry. Other cut connections reject.
- [x] GREEN selection/source/parser/D1 tests; two-stage review and scoped commit.

P3 measured record (2026-10-16): staged RED/GREEN batches produced 154 new tests;
the parent updated P2's fixed reason-enum test to the newly allowed fixed codes,
then the combined four modules passed 619 tests. SPEC PASS included 871 additional
synthetic probes. QUALITY initially rejected conflicting reciprocal CONECT bond
multiplicities, which could incorrectly authorize excluding an alternate edge.
The repair's new batch ran 50 failed / 16 passed before the fix, then 66 passed.
Final worker, parent and independent quality runs each obtained **685 passed**;
QUALITY APPROVED included 320 extra combinations and a preallocation-budget probe.
Compilation and diff checks passed. Errors remain fixed codes without caller text.

P3 inspects and validates explicit choices only. Public inspection still says
`input_ready=False` and `source_authentication=caller_responsibility`. P4's public
projection/verification functions, P6's official source authentication and all
actual input/consent/browser docking gates remain unfinished.

## P4 — Deterministic derivative, complete lineage and pocket verification

**Files:** receptor_projection.py / receptor_projection_pdb.py and their tests.

- [x] Add RED asserting byte stability, original immutability and retained
  occupancy/xyz/B-factor/element/charge preservation. In the synthetic A choice,
  derive one complete ALA, remove B/reference/explicitly removed water, and never
  infer that the removed reference atoms are missing receptor atoms.

```python
def test_derivative_has_complete_original_atom_partition(full_fixture):
    from src.docking.receptor_projection import project_receptor_bytes
    from tests.test_receptor_projection import selection as make_selection
    pdb, source = full_fixture
    selection = make_selection()
    first = project_receptor_bytes(pdb, source=source, selection=selection)
    second = project_receptor_bytes(pdb, source=source, selection=selection)
    assert first["ok"] and first == second
    rows = first["projection"]["lineage"]
    assert len(rows) == sum(len(r["atoms"]) for r in source["residues"])
    assert len({r["source_serial"] for r in rows}) == len(rows)
    assert all((r["derived_serial"] is None) == (r["disposition"] == "removed") for r in rows)
    assert first["projection"]["schema_version"] == "receptor-projection-v1"
    assert b"  0.60" in first["pdb_bytes"]
```

- [x] Implement deterministic serial/TER assignment in original retained order.
  Reserve counts/bytes before output; serial overflow rejects. The only coordinate
  line edits are a documented serial rewrite and clearing the chosen altloc:

```python
def projected_atom_line(raw_line: bytes, serial: int) -> bytes:
    line = raw_line.rstrip(b"\r\n")
    return line[:6] + f"{serial:5d}".encode("ascii") + line[11:16] + b" " + line[17:] + b"\n"
```

- [x] Preserve original chain/segment boundaries with valid TER records; emit a
  single model envelope and END. Reconstruct supported retained connectivity with
  exact endpoint mapping and cleared chosen altloc labels; never emit dangling
  LINK/SSBOND/CONECT. Drop original annotation records only under the fixed,
  declared metadata policy; keep their evidence in the immutable original source.
  No coordinate, atom-identity, charge, occupancy or B-factor field is rewritten.
- [x] Compute the pocket only from selected, fully occupied reference heavy atoms:

```python
reference_heavy_xyz = [tuple(value / 1000.0 for value in atom.xyz_milli)
                       for atom in selected_reference_atoms if atom.element not in {"H", "D"}]
minimum = [min(xyz[axis] for xyz in reference_heavy_xyz) for axis in range(3)]
maximum = [max(xyz[axis] for xyz in reference_heavy_xyz) for axis in range(3)]
center = [(low + high) / 2.0 for low, high in zip(minimum, maximum)]
size = [max(10.0, high - low + 8.0) for low, high in zip(minimum, maximum)]
if any(value > 100.0 for value in size):
    raise ProjectionInvalid("selection_invalid")
```

  Preserve D1's `heavy-aabb-margin4-min10-v1`, source atom identities and Å units;
  do not call the old strict source entry with a forged occupancy-1 descriptor.
- [x] Reparse the complete derivative with the new parser and compare every
  retained field/connection to the selected original. Implement
  `verify_receptor_projection` by recomputing the deterministic result and comparing
  both bytes and the bounded closed descriptor. Equal hashes alone are insufficient.
- [x] RED/GREEN tampered coordinates/occupancy/lineage/removal reason, duplicate or
  missing lineage, CRLF input/LF output, shuffled choice ordering canonicalization,
  different selected conformer, large escaped source strings, serial/byte overflow,
  stale metadata, dangling links and false `receptor-projection-v1` labels.
- [x] GREEN all three new modules and D1 regression; two-stage review and commit.

### P4 execution evidence (2026-10-16)

The worker observed missing-callable RED batches of 38 and 6 failures, followed
by three hardening RED batches (3, 2 and 1). Final P1–P4 plus strict D1 regression
passed 779 tests. Independent SPEC PASS reproduced 779 tests and 1,646 adverse
probes; fresh QUALITY APPROVED reproduced 779 tests and 1,870 probes. Parent
verification of the same four test modules passed 779 tests in 13.11 seconds.

This implements deterministic derivative bytes, complete original-atom lineage,
supported connectivity and exact pocket recomputation only. It does not
authenticate a source, select a scientific input, grant consent, run docking or
close the P5–P7 integration and real-browser gates.

## P5 — Owned preparation dispatch, without scientific execution

**Files:** chat_preparation.py and test_chat_docking_preparation.py.

- [ ] RED actual `run_preparation` calls for `projection_inspect`, `projection` and
  `projection_verify`, using a retained `PreparationOwner`, actual process start
  and the original absolute monotonic deadline. No mock result envelope.
- [ ] Extend only the exact operation/argument map and producer dispatch:

```python
_INPUTS.update({
    "projection_inspect": {"pdb_bytes", "source"},
    "projection": {"pdb_bytes", "source", "selection"},
    "projection_verify": {"pdb_bytes", "source", "selection", "derived_bytes", "projection"},
})
```

  Dispatch respectively to the three public functions defined above, imported
  lazily in the child. Run resource preflight before spawning/serializing this
  input family. Existing ligand/inspect/pocket/receptor dispatch stays unchanged.
- [ ] RED/GREEN cancellation before/after physical spawn, late worker result,
  double cancellation, blocked cleanup helper and expiry during settlement. A
  late success is not published, owner remains retained while any helper is active,
  and no ADFR/Meeko/Vina command or raw-input diagnostic can occur here.
- [ ] Run `python -m pytest tests/test_chat_docking_preparation.py
  tests/test_docking_pocket_evidence.py tests/test_receptor_projection.py
  tests/test_receptor_projection_source.py tests/test_receptor_projection_pdb.py -q`.
  Add existing process-isolation and command-cancellation suites for final review.
  Independent spec/quality review, then explicit scoped commit.

## P6 — D2–D6 authority, preview and invalidation integration gate

**Files:** the exact D2–D6 files/tests listed in the parent chat-docking plan.
Do not create a parallel coordinator, extra receiver or generic execution API.

- [ ] Before D2 calls any projection producer, establish authenticated same-version
  source facts from official atom/connection/component evidence and current S03
  target/structure evidence. Verify this against the original PDB, not a filtered
  file or a descriptor rebuilt from that PDB. No identity, version or absent-atom
  fact may be invented to satisfy the pure contract.
- [ ] Add actual coordinator RED tests: self-consistent forged source with correct
  hashes, wrong owner/session/root, stale source version/generation, wrong species,
  missing independent inventory, fabricated ligand role and altered component
  definition cannot reach the preparation dispatch or mark an input READY.
- [ ] Require all selection fields from current bound preview choices. Persist
  immutable original-source and selection digests; keep full atom lineage as an
  owned bounded artifact, not a huge model message or client authority token.
- [ ] The existing homepage preview must display target/species/chain/construct,
  every conformer/hetero choice, all partial occupancies and applicable limitations,
  reference residue/connection checks and original/derived identities. Use existing
  owner-bound paging if lists exceed display limits; never silently show only a
  prefix while claiming all choices were reviewed. Rendering is textContent-only.
- [ ] Stage exact verified derivative bytes and preserve the projection kind in
  source refs. Read back staged bytes under retained ownership and run the explicit
  projection verification path before READY. Unsupported downstream preparation
  is a failure, not permission to normalize occupancy, repair or use a fallback.
  Keep the legacy strict input family distinct; a failed old parse is never an
  automatic opt-in to this family without its complete authenticated source and
  explicit selection. No direct call accepts a client-provided projection kind.
- [ ] Actual store/route tests must change each of source bytes, version, selection,
  rule and derived input after READY: the prior request/nonce becomes invalid;
  a new explicit request and current preview are required. A draft refinement
  still uses the original expiry/budget and cannot revive spent approval.
- [ ] Run D2–D6 real SQLite/session/route/DOM negative suites, including duplicate
  approval, lost response, cancellation, cross-owner reads and alternate-path
  isolation. Only then allow the parent feature's activation gate to pass.

### P6 transport implementation slice

The existing downloader's path-returning cache operation is not paired revision
evidence. Add only `fetch_rcsb_source_pair(pdb_id, *, deadline)` to
`src/target_search/downloader.py`, with focused tests in
`tests/test_docking_source_download.py`. This synchronous internal producer will
later run inside the retained source-preparation worker, never on the event loop
or in a detached cancellation-only thread. It does not create a second execution
API or change legacy downloader/cache behavior.

```python
candidate = fetch_rcsb_source_pair(server_selected_pdb_id, deadline=root_deadline)
assert set(candidate) == {"pdb_id", "pdb_bytes", "cif_bytes", "pdb_sha256", "cif_sha256"}
```

- [ ] RED: exact uppercase four-character PDB identifier and finite native deadline
  are required before DNS; reject URLs, paths, subclasses and extra arguments.
- [ ] Build only `https://files.rcsb.org/download/{pdb_id}.pdb` and `.cif`, reuse
  existing trusted-host request guards, reject redirects and non-200 responses,
  and close each acquired response on all exits. No retry, decompression,
  alternate candidate, fallback conversion, cache/DB write or response diagnostic.
- [ ] Stream bytes with 16 MiB per file and 32 MiB aggregate caps; cap the
  Content-Length decimal before parsing, reject malformed/contradictory lengths,
  non-byte chunks, non-identity Content-Encoding, empty or truncated bodies.
  Do not use response.content/text or preallocate from an untrusted length.
- [ ] Use the same absolute deadline before/after request, each chunk and close;
  give each request only remaining time, capped at the existing 30 seconds.
  Check before publication too. Deadline checks do not terminate a blocked socket;
  only the subsequent retained process integration can guarantee cancellation.
- [ ] GREEN: exact byte preservation, digests, second-file failure without partial
  publication, malformed lengths/encodings, overflow, timeout and all response
  closures. Run this new test module plus existing downloader negatives; obtain
  independent spec/quality review before commit.

Transport evidence (2026-10-16): missing-helper/response-leak RED had 96
failures. Independent SPEC then found three DNS/HTTP deadline gaps; parent
reproduced all three before moving deadline checks to the actual request
boundary. Fresh QUALITY found Requests redirect preprocessing could buffer
the body even with `allow_redirects=False`; four real-Session/fake-adapter
regressions consumed 17,827,840 bytes before the new-path-only response hook
was added. All four now reject and close before any body read.

Final parent source/security/cache regression: 118 passed in 1.59 seconds.
Independent SPEC passed 118 tests and 11 deadline probes before the response-hook
fix; final QUALITY APPROVED passed 122 tests (including four legacy cases) plus
23 real-Session probes. Legacy requests receive no new hook; an omitted/None
deadline retains their prior behavior. A separate parent joint run passed 1,773
tests in 82.54 seconds, but predates the hook and subsequent P5 outer-map fix;
it is not the final combined-head verification. This closes only the synchronous
transport slice, not the retained source worker or authentication.

The returned candidate is not authenticated: matching IDs/hashes/download times
cannot establish same revision or ownership. P6 must still verify exact same-entry
revision/complete inventory/chemistry, bind live S03 evidence and require explicit
scientific choices before any projection or READY state.

### P6 official evidence compiler boundary

Keep the source-format/chemistry comparison separate from Web ownership in
`src/docking/rcsb_source.py` and `tests/test_rcsb_source.py`. The compiler is
pure and never authenticates caller-provided bytes by itself. Only the retained
server source owner may bind its output to actual canonical official downloads
and revalidated current S03 evidence. No browser/model payload supplies this
compiler's bytes, component records, target identity or authority token.

```python
candidate = compile_rcsb_source(
    pdb_bytes, cif_bytes, component_files,
    pdb_id=server_selected_pdb_id,
    target={"uniprot_id": verified_accession, "taxonomy_id": verified_taxonomy},
)
```

- [ ] Add missing-callable RED, then strict native immutable-byte admission and
  bounded Gemmi 0.7.5 CIF parsing in the owned child. Pin `gemmi==0.7.5` in both
  root CPU and deployment GPU requirements; no model/repair/transformation API.
  Reject duplicate tags/rows, unsupported frames/multiple blocks, malformed
  references, unknown required chemistry, and nonfinite/overprecision numbers.
- [ ] Match the PDB ID, all first REVDAT lines and their revision dates to the
  complete mmCIF Structure-model revision history. Use exact current original
  bytes. Compare the complete independent atom-site inventory one-to-one with
  P1, including all models, waters, alternates, occupancies, coordinates, B
  factors, elements and charges. PDB serials are mapped only after exact atom
  identity equality; mmCIF atom IDs are not assumed equal to PDB serials.
- [ ] Verify entity/label/auth chain and sequence mappings, deposited polypeptide
  type, UniProt mapping and biological-source taxonomy (not expression host).
  Carry missing-residue, sequence-variant and independently evidenced construct
  limitations. Do not infer absent full-protein sequence outside proved ranges.
- [ ] Require every embedded component's official CCD bytes. Bound component
  count and aggregate bytes before parsing; require released component identity,
  explicit modification date no later than the entry revision, and exact
  same-entry atom name/element/aromatic/stereo plus bond endpoint/order/aromatic/
  stereo equality. Record each exact CCD digest/version separately. Post-revision
  or conflicting definitions are unavailable, not silently normalized.
- [ ] Derive required heavy atoms from those component definitions and explicit
  deposited bond context. Standard linear peptide OXT displacement requires the
  complete polymer sequence/scheme and actual linking-component definitions;
  terminal OXT remains required. Glycan displacement requires explicit branch
  leaving-atom fields or matching deposited modification/connection evidence
  plus uniquely identified CCD leaving atoms. An absent coordinate is never
  evidence that an atom was a leaving group. Unsupported context fails closed.
- [ ] Classify protein and water from independent entity/component facts;
  `deposited_ligand` requires exact same-entry SUBJECT OF INVESTIGATION feature
  and compatible nonpolymer identity. Glycan needs deposited branch/modification
  evidence. Other roles remain unproven unless independently supported. No
  arbitrary HETATM or primary component name is promoted to pocket reference.
- [ ] Resolve every PDB LINK/SSBOND/CONECT edge using independent struct_conn or
  matching intracomponent CCD bonds, including symmetry and multiplicity; also
  require every applicable deposited inter-residue connection to be accounted
  for. Do not infer covalency from distance or PDB record type. Unresolved
  cross-residue alternate coupling is unsupported, not an empty constraint list.
- [ ] Return the complete `receptor-source-v1` candidate plus bounded evidence
  identities/digests and explicit limitations. Reserve input, native parser,
  indexes, output and IPC capacity on one conservative logical ledger before
  allocation; native parser overhead is a reservation, not an OS RSS guarantee.
  Run P1/P2 on the completed candidate without altering either strict contract.
- [ ] Actual malformed-version, hidden-atom, role, taxonomy, component, terminal,
  glycan, link and alternate negative tests must fail before repair. Independently
  authored synthetic fixtures prove contracts only. A read-only official-source
  comparison may inform feasibility, never replace the eventual current-owner
  browser source selection or count as S10. Obtain spec then quality review.

## P7 — Real source selection, browser S10 and release evidence

**Files:** existing browser acceptance validation report and this plan's ledger.

- [ ] On the agent-owned service only, repeat actual S03 and show the current
  experimental structures. Fetch/verify a selected structure through D2; no
  hardcoded hidden candidate, terminal-prepared substitute or silent mmCIF-to-PDB
  conversion. Existing PDB 404/missing chemistry remains explicit unavailable.
- [ ] Obtain current explicit residue/conformer/hetero/limitation choices and the
  bound runtime approval. Development/merge permission is not itself the runtime
  consent record. No hard scientific failure has an authorization override.
- [ ] Submit original S10 in the same normal homepage chat, observe the same task,
  compare actual finite energy/pose count/source and output hashes with authorized
  downloaded artifacts. Do not substitute a separate command-line Vina run.
- [ ] If no verified usable source exists, continue safe diagnostics and other
  required work, but keep S10 BLOCKED; never rename a diagnostic or synthetic
  fixture as a successful real run. Report any actual irreducible dependency.
- [ ] Run complete current offline tests, compileall, changed JS syntax, relevant
  Node scripts and strict health check; obtain independent final integration review.
  Record every skip/failure and do not reuse the old 7ab815de full-suite result.
- [ ] Audit all R1–R5/D1–D7/P1–P7 requirements against actual current evidence.
  Explicitly stage task files only, push PR #209, verify required remote checks and
  review state, then merge only when the authorized completion gates are satisfied.

## Test commands and evidence rules

Use the installed MedChat scientific Python environment. Example PowerShell
invocation for the pure/worker gate after P5, with a fresh temporary root:

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$testRoot = Join-Path $env:TEMP ('medchat-projection-' + [guid]::NewGuid())
python -m pytest tests/test_receptor_projection_pdb.py tests/test_receptor_projection_source.py tests/test_receptor_projection.py tests/test_chat_docking_preparation.py tests/test_docking_pocket_evidence.py -q --basetemp $testRoot -p no:cacheprovider --tb=short
python -m compileall -q src scripts
```

Before implementation the new module tests must fail for the absent behavior.
After each task, the selected tests must pass with exit code 0; existing safe
behavior may already be GREEN and must not be deliberately broken to manufacture
RED. Do not assert a future test count. Sandbox temporary-directory failures are
environment failures, not product results; rerun the same bounded test with the
required temporary-directory access and preserve both records.

The scientific format references are the official
[wwPDB coordinate fields](https://www.wwpdb.org/documentation/file-format-content/format33/sect9.html),
[connection annotations](https://www.wwpdb.org/documentation/file-format-content/format33/sect6.html)
and [connection-type dictionary](https://mmcif.wwpdb.org/dictionaries/mmcif_pdbx_v50.dic/Items/_struct_conn.conn_type_id.html).
In particular, LINK may describe coordination rather than an ordinary covalent
bond, and SSBOND lacks an alternate-position field. Treat ambiguous semantics as
unsupported; do not infer chemistry solely from record presence or distance.

## Self-review and progress ledger

- [x] Approval recorded; original strict entry and scientific/authority boundaries retained.
- [x] File responsibilities and all new function/data contracts defined above.
- [x] Spec mapping: original/source boundaries → P1/P2/P6; scientific choices → P3;
  derivative/lineage/pocket → P4; lifecycle/budgets → P2/P5; preview/approval → P6;
  real acceptance/exclusions → P7.
- [x] Plan self-review: no automatic conformer/default role, coordinate repair,
  authority-by-hash, stale consent reuse or narrow-test-as-full-acceptance claim.
- [ ] P1–P7 implementation, RED/GREEN evidence and independent reviews completed.

This plan does not itself implement preprocessing, close R3/R4, produce scientific
results or change the current browser result of 11 PASS / 3 BLOCKED.

### Read-only source feasibility triage (2026-10-16)

A public-coordinate diagnostic revisited the 113 P06276 search entries: 92 PDB
reads succeeded and 21 returned HTTP 404. This was not the new parser, independent
inventory authentication or a selected docking input. Screening complete raw
files for zero-occupancy ATOM records, missing-atom annotations and observed
nonpolymer coordinates identified further candidates for review; absence of an
annotation or LINK record does not prove completeness or noncovalent chemistry.

For example, unchanged 6SAM had 4,521 coordinate records, 46 alternate-location
records, no zero-occupancy ATOM record and no parsed missing-atom annotation row.
L3H at author chain A/residue 603 had 25 observed heavy atoms at occupancy 1.00
and no raw LINK endpoint. The official [6SAM entry](https://www.rcsb.org/structure/6SAM)
independently identifies L3H as a ligand of interest and its primary citation
reports noncovalent inhibition. This makes it a candidate for the complete P2/P6
checks, not a validated receptor, chosen structure, conformer choice or consent.
Construct mutations, absent residues, chemical-component completeness and every
retained glycan/connection still require authenticated evidence and explicit review.

Conversely, [1XLU](https://www.rcsb.org/structure/1XLU) illustrates why a raw LINK
screen alone cannot authorize ligand removal: its entry describes an aged
organophosphate-inhibited enzyme, despite the diagnostic not finding an ISP LINK
endpoint. No source was edited, no derivative was created and no Vina ran.

Further read-only feasibility check: unchanged official 6SAM PDB parses with P1
(4,521 atoms, 245 connection records). Its complete ordered identity/record/name/
element/altloc/occupancy/xyz/B-factor inventory agrees with the independently
downloaded official mmCIF atom table. The PDB includes one atom-serial gap at TER;
mmCIF atom IDs must not be assumed to equal PDB serials. Both files report the
2024-11-20 revision (entry major/minor 2.2 in mmCIF). PDB SHA-256 is
`471ee4ee714e13da6c07a895bcf2dd5cc29859623fe5942236c65499ced8e7f1`;
mmCIF SHA-256 is `bb0e444acca25f86861aa5003a9353c6e73b3b044f0cc84adb6e23856e67c878`.

The independent metadata reports 16 `struct_conn` records, 27 embedded chemical
components, three unobserved residues and four engineered sequence variants.
Its embedded `chem_comp_atom` table does not include leaving-atom flags; branch
link metadata names specific leaving atoms. Full deposited chemical context,
terminal atoms and protein–glycan connections still need explicit validation;
neither a successful atom-table comparison nor a CCD component name proves that
required heavy atoms are complete. This diagnostic used the already-installed
Gemmi 0.7.5 in memory only, not a production source-authentication implementation,
a selected S03 input, a prepared receptor or a passed S10. No files were converted.
Source format references: [official atom-site field relationships](https://mmcif.rcsb.org/docs/tutorials/content/atomic-description.html)
and [RCSB file download services](https://www.rcsb.org/docs/programmatic-access/file-download-services).

A further memory-only inventory comparison on the same official mmCIF digest
found 631 heavy-atom-bearing residues and 27 embedded component definitions.
Before applying any deposited bond/terminal context, the only absent heavy-atom
names were OXT in standard amino-acid components and O1 in nine NAG/four FUC
residues. The final polymer scheme residue is VAL529. Partial-occupancy heavy
atoms occur in HIS and SER alternate conformers. This is evidence for the next
authentication design, not permission to remove those requirements: peptide
terminal context and each glycan leaving atom must still be justified from the
same deposited chemistry, never inferred merely from an absent coordinate.
No conformer or hetero choice was made, no descriptor was authenticated, and no
input was projected or executed by this diagnostic.

Subsequent same-digest checks established that terminal VAL529 actually contains
OXT; the earlier aggregate missing-OXT summary must not be applied to that
terminus. The three alternate residues are HIS214, HIS372 and SER466. The
same mmCIF has a `pdbx_entity_instance_feature` SUBJECT OF INVESTIGATION record
for L3H, so its role need not be guessed from a component name. Twenty-seven
official CCD records were read in memory: their complete atom name/element/
aromatic/stereo tables and bond endpoint/order/aromatic/stereo tables matched
the same-entry embedded tables. All reported CCD modification dates were no
later than the entry revision date. NAG/FUC CCD leaving flags explicitly include
O1; the actual residue-specific linkage must still justify its displacement.
These observations guide the verifier, not a special-case acceptance of 6SAM.

The standard-format parser integration will use Gemmi 0.7.5 under the later
finish/default-authorization instruction. This introduces no repair, coordinate
conversion, model or scoring behavior. Both dependency profiles must declare
the parser when its production adapter is implemented; missing/other versions
must not silently select a permissive alternate parser. The exact version's
[LICENSE.txt](https://github.com/project-gemmi/gemmi/blob/v0.7.5/LICENSE.txt)
is MPL-2.0; do not copy the different license label from a generic docs index.
