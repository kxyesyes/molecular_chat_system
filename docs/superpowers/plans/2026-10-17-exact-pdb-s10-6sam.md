# Exact PDB lookup and 6SAM acceptance implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve an explicit PDB constraint through authoritative lookup and complete one real, user-authorized 6SAM docking in the ordinary browser flow.

**Architecture:** Add optional keyword-only `pdb_id` to the existing tool/service/resolver/RCSB path. Preserve default call shapes; exact requests bypass local broad matches and use isolated, identity-validated structure caches. Reuse source, projection, approval and artifact gates unchanged.

**Tech Stack:** Python 3.10, FastAPI, SQLite, bounded Requests clients, existing vanilla JS/Jinja2, RDKit/ADFR/Meeko/Vina.

---

## Task 1: Bounded exact RCSB query

Files: `src/target_search/remote_clients.py`, new `tests/test_rcsb_pdb_exact.py`.

- [x] Write and run failing tests for `RcsbClient.search("P06276", pdb_id="6SAM")`, invalid identifiers and nonmatching returned entity IDs.
- [x] Add the optional keyword without changing default payloads. Append this official search-schema terminal only for exact calls:

```python
{"type": "terminal", "service": "text", "parameters": {
    "attribute": "rcsb_polymer_entity_container_identifiers.entry_id",
    "operator": "exact_match", "value": "6SAM"}}
```

- [x] Validate raw PDB IDs before network operations; normalize case, reject prefixes/paths. Reject hits for another entry, preserving existing polymer identity/GraphQL validation, operation deadlines and result bounds.
- [x] Run `python -m pytest tests/test_rcsb_pdb_exact.py -q --basetemp scratch/exact-pdb-client`; review spec, then quality independently.

## Task 2: Tool, resolver, service and cache binding

Files: `src/agent/tools/target_database_tool.py`, `src/target_search/authoritative_resolver.py`, `src/target_search/service.py`, new `tests/test_target_pdb_exact.py`; relevant planner input binding only if inspection demonstrates a dropped constraint.

- [x] Establish RED tests. Core service test shape:

```python
result = service.search_targets("P06276", pdb_id="6SAM")
assert result["status"] == "resolved"
assert [s["structure_id"] for s in result["results"][0]["recommended_structures"]] == ["6SAM"]
```

Include positive labeled/structured requests; invalid, conflicting and negated labels; local broad matches; warm generic cache; wrong entry, target or organism; unavailable vs not-found; exact miss without AlphaFold; and no contamination of generic caches.
- [x] Thread optional keyword-only `pdb_id` through service/resolver; default calls omit it to retain compatible test-double and caller contracts. Exact calls must not return a local broad hit, a different structure or AlphaFold fallback.
- [x] Use an exact structure cache key bound to accession/organism/PDB. Revalidate cache envelope, normalized identity and exact structure set on reads; never write a one-entry exact result into `structures:<accession>`.
- [x] Parse a single explicit PDB label or structured `pdb_id`. Reject invalid/conflicting/qualified requests instead of silently dropping constraints. Inspect the original-query-to-tool binding; ensure the model/planner cannot remove the explicit constraint.
- [x] Run focused tests, then affected target/source regressions and compileall. Do not run unrelated previously passed suites. Obtain independent spec and quality review, fix findings with failing tests, commit only task files.

## Task 3: Live ordinary-browser verification

No credentials, raw chat, runtime databases or downloaded structures are committed.

- [ ] Restart only the verified owned 6001 service with the existing isolated Python/toolchain/asset configuration. Wait for this process's real full-source prewarm.
- [ ] In ordinary chat query human P06276 with `PDB: 6SAM`; verify actual accepted current source contains exactly that experimentally determined entry.
- [ ] Submit the existing aspirin SMILES docking request for 6SAM, using deposited L3H to define the pocket. Review all source choices and applicable limitations, then explicitly choose the protein, reference, hetero actions and conformers without repair or guessed coordinates.
- [ ] Inspect input provenance, derived hashes, ligand preparation and pocket geometry; confirm parameters then the current READY through the UI, as expressly authorized by the user.
- [ ] Compare real Vina executable/version, successful physical completion, original energy table and pose count with the visible result. Inspect actual pose geometry and validated structure rendering.
- [ ] Download through the visible artifact link. Compare actual downloaded bytes/size/hash with published artifact and original output; check molecule/pose counts and scores. No terminal execution or fabricated result substitutes for this flow.
- [ ] Update validation/handoff with precise files, commands, failures, skipped checks, browser evidence and actual scientific outputs. Push to original Draft PR #209; do not merge.

## Self-review

The optional API shape, exact-cache isolation, negative results and browser authority are specified. Task 1 is independent of Task 2 through the named keyword contract; parent handles integration while a worker handles only the bounded client. Scientific preparation and user-authorized UI confirmation remain separate from lookup. No new framework, model permission, repair algorithm or unbounded network policy is introduced.
