# Authoritative Target Search Fallback Design

## 1. Objective

MedChat must continue a target-driven scientific workflow when the local target
database is empty or does not contain the requested target, without allowing the
main language model to invent target records, structures, identifiers, or docking
readiness claims.

The target-search chain will be local-first and evidence-driven:

1. Search the local SQLite target database.
2. If the database is empty, import the repository seed CSV files idempotently and
   retry the local search.
3. If the target is still absent, resolve it through UniProt.
4. Use the resolved UniProt accession to search RCSB PDB experimental structures.
5. If no suitable experimental structure is available, query AlphaFold DB for a
   predicted structure.
6. Normalize accepted remote evidence into the existing target-search contracts,
   cache it with provenance and expiry metadata, and continue the workflow.
7. If no trustworthy evidence is available, stop downstream target-driven
   generation and report a blocked or partial result.

## 2. Design Principles

- Scientific claims come from deterministic database clients, never from the main
  chat model.
- Experimental and predicted structures remain explicitly distinguishable.
- Local results are preferred for latency and reproducibility.
- Remote responses are normalized before they enter Agent semantic gates.
- A remote outage cannot erase or overwrite valid local data.
- Stale cache use is allowed only when the authoritative source is unavailable and
  must be visible in warnings and provenance.
- No remote lookup may silently convert failure, rate limiting, malformed payloads,
  or missing evidence into success.

## 3. Components

### 3.1 TargetSearchService

`TargetSearchService.search_targets()` remains the public search entry point. It
will coordinate a new resolver without embedding HTTP protocol details in the
service itself.

Search behavior:

- Run the existing exact and fuzzy local search.
- If the local database contains no target rows, invoke the existing seed importer
  once under a process-safe initialization guard, then retry.
- If a populated local database has no match, invoke the authoritative resolver.
- Return a normalized payload with `results`, `warnings`, `lookup_path`, and cache
  state.

Filters that can only be evaluated after structure retrieval are applied to the
normalized remote result before it is returned.

### 3.2 AuthoritativeTargetResolver

A focused resolver module will own remote lookup orchestration. Its dependencies
are injectable clients so tests do not require network access.

Resolution order:

1. UniProt exact gene/alias/accession lookup restricted to the requested organism;
   human is the default only when the prompt does not specify an organism.
2. RCSB Search API lookup by UniProt accession.
3. RCSB Data API metadata retrieval for a bounded number of candidate entries.
4. AlphaFold DB lookup by UniProt accession when no suitable experimental record is
   found.

Ambiguous UniProt matches are not guessed. The resolver returns a structured
ambiguity result and asks the user to identify the organism or target.

### 3.3 Source Clients

Three small clients will isolate external API contracts:

- `UniProtClient`: target identity, canonical accession, protein name, gene aliases,
  organism, and source URL.
- `RcsbClient`: experimental structure identifiers and metadata including method,
  resolution when available, organism, ligand evidence, and download URL.
- `AlphaFoldClient`: predicted structure availability, model identifier, source URL,
  and coordinate download URL.

Each client will use finite connect/read timeouts, bounded retries for transient
errors and HTTP 429, exponential backoff, response-size limits, and explicit user
agent identification. Authentication is not required for these public sources.

### 3.4 Cache Repository

Remote evidence will be stored through a dedicated cache repository instead of
mixing cache policy into SQL search code.

Required cache fields:

- source and source record identifier;
- source URL;
- normalized target or structure payload;
- retrieval timestamp;
- expiry timestamp;
- last refresh status;
- stale flag;
- payload digest;
- schema version.

Cache durations:

- target identity and metadata: 30 days;
- structure-search metadata: 7 days;
- downloaded coordinate files: 90 days.

Fresh cache entries are used without network access. Expired entries trigger a
refresh. If refresh fails transiently, the expired entry may be returned with
`stale=true` and a warning. Invalid or contradicted entries are never used as stale
fallbacks.

The cleanup operation removes expired metadata and unreferenced structure files. It
must only delete paths under the configured target cache root.

## 4. Data Contracts and Provenance

Normalized target evidence must contain at least:

- `gene_symbol`;
- `protein_name`;
- `uniprot_id`;
- `organism`;
- `source`;
- `source_record_id`;
- `source_url`;
- `retrieved_at`;
- `expires_at`;
- `stale`;
- `match_reason`.

Normalized structure evidence must additionally contain:

- `structure_id`;
- `structure_type` (`experimental` or `predicted`);
- `experimental_method` when applicable;
- `resolution` when available;
- ligand identifiers when available;
- coordinate download URL;
- docking-readiness fields and their evidence basis.

The ToolResult adapter will preserve these values in `data`, `evidence`, `quality`,
and `warnings`. Remote evidence is acceptable to the existing `target_evidence`
semantic gate only when it contains a stable target identifier and a recognized
source.

## 5. Workflow Behavior

For a prompt such as “针对 PDE5A 设计 10 个候选小分子，筛选最适合后续
docking 的前 3 个”:

1. Router selects `target_driven_design`.
2. Planner creates target search, generation, properties, ADMET, activity, and
   ranking steps.
3. Target search follows the local/seed/remote chain and emits provenance.
4. Generation receives normalized target evidence and the explicit requested count.
5. Candidate property, ADMET, and activity tools consume the generated candidate
   set through existing bindings.
6. A deterministic ranking component consumes aligned candidate assessments and
   selects up to `docking_top_n` candidates.
7. Ranking may use only successfully calculated fields. Missing activity or ADMET
   lowers evidence completeness and produces warnings; it does not invent values.
8. No docking score or binding energy is produced unless the docking tool actually
   runs with complete receptor, ligand, and box inputs.

The ranking result must state its criteria and evidence completeness. If fewer than
the requested number of valid unique molecules are generated, the workflow returns
partial results and ranks only the validated candidates.

## 6. Status and Error Semantics

- Local miss followed by remote success: completed, with lookup-path provenance.
- Remote success using stale cache after transient outage: partial or completed with
  an explicit stale warning, depending on whether all required evidence remains
  valid.
- Ambiguous target identity: blocked, requesting clarification.
- No authoritative target evidence: partial when an earlier useful result exists,
  otherwise failed.
- Remote timeout, rate limit, or schema error: explicit source-specific warning and
  failure details.
- Generation blocked by `target_evidence`: terminal event message must describe the
  failed precondition; it must not say `Workflow completed`.

Terminal progress of `1.0` means execution has ended, not that it succeeded. The UI
must derive success styling from the terminal event type and outcome.

## 7. Configuration and Operations

External lookup is enabled by default but can be disabled through configuration for
offline or regulated deployments. Configuration includes:

- source base URLs;
- connect and read timeouts;
- maximum retries and backoff ceiling;
- cache TTL values;
- maximum structures fetched per target;
- default organism;
- cleanup batch size.

A health report will separately expose local row counts, seed availability, remote
source reachability, cache counts, stale counts, and last refresh errors. It will
never include API keys or unrelated environment values.

## 8. Testing Strategy

Implementation will follow red-green-refactor TDD.

Unit tests will cover:

- empty SQLite database triggers idempotent seed import;
- local hit avoids all remote calls;
- local miss resolves UniProt then RCSB;
- RCSB miss falls back to AlphaFold;
- ambiguous identity blocks rather than guessing;
- timeout and rate-limit responses preserve errors;
- fresh, expired, and stale cache behavior;
- cache cleanup cannot escape the cache root;
- normalized remote evidence passes the semantic evidence gate;
- no evidence blocks generation;
- partial terminal events carry partial messages;
- requested candidate count reaches generation;
- Top-N ranking consumes aligned tool outputs;
- ranking never creates docking energies.

Integration tests will use a temporary seeded database and deterministic HTTP
fixtures. A separately marked real-network acceptance test will query PDE5A and
verify a UniProt accession plus at least one RCSB or AlphaFold structure source. Real
network failure is reported as skipped or failed, never converted to a fixture pass.

## 9. Non-goals

- The main LLM will not browse arbitrary websites for scientific evidence.
- This change will not automatically run docking.
- It will not download every available structure.
- It will not replace curated local records with remote records.
- It will not implement general literature search or experimental claim extraction.

## 10. Acceptance Criteria

- PDE5A can be resolved when the local SQLite database is empty.
- Every returned target and structure record is traceable to local seed data,
  UniProt, RCSB PDB, or AlphaFold DB.
- Cache expiry and stale behavior follow the agreed 30/7/90-day policy.
- A remote outage produces an honest partial/failed result with warnings.
- The PDE5A design workflow reaches molecule generation only after validated target
  evidence exists.
- Ten requested candidates and Top 3 selection are enforced through executable data
  flow rather than unused planner metadata.
- No unexecuted docking score or binding energy appears in the result.
- Existing unrelated working-tree changes remain untouched.
