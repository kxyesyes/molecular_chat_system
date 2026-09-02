# Authoritative Target Search Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make target-driven workflows recover from an empty or incomplete local target database through traceable UniProt, RCSB PDB, and AlphaFold lookups, enforce expiring caches, propagate honest partial outcomes, and execute deterministic Top-N candidate ranking.

**Architecture:** Keep `TargetSearchService` as the public facade, add small injectable HTTP clients plus an authoritative resolver, and store normalized remote payloads in a dedicated SQLite TTL cache. Extend workflow bindings with a structured workflow payload so generation and ranking consume explicit planner metadata and upstream outputs. Preserve semantic gates: downstream generation starts only after authoritative target evidence exists.

**Tech Stack:** Python 3.10+, SQLite, requests, FastAPI-era Pydantic contracts, pytest/unittest, RDKit-backed candidate validation, existing Agent ToolResult and workflow runtime.

---

## File Map

**Create:**

- `src/target_search/cache.py` — SQLite TTL cache repository and safe cleanup.
- `src/target_search/remote_clients.py` — UniProt, RCSB, and AlphaFold HTTP clients.
- `src/target_search/authoritative_resolver.py` — deterministic multi-source orchestration and normalization.
- `src/agent/tools/candidate_ranker.py` — deterministic evidence-aware Top-N ranking tool.
- `scripts/cleanup_target_cache.py` — bounded manual cache cleanup command.
- `tests/test_target_search_fallback.py` — seed, remote fallback, cache, and provenance tests.
- `tests/agent/test_candidate_ranker.py` — ranking and workflow data-flow tests.

**Modify:**

- `src/target_search/database.py` — add remote-cache and runtime-state schema.
- `src/target_search/service.py` — local/seed/cache/remote search chain.
- `src/target_search/downloader.py` — register downloaded coordinates with a 90-day cache lifetime.
- `src/agent/tools/target_database_tool.py` — preserve lookup warnings, provenance, and remote structures.
- `src/agent/runtime/run_session.py` — emit the final partial/failed message instead of a stale success message.
- `src/agent/planning/bindings.py` — expose a structured workflow binding.
- `src/agent/planning/task_planner.py` — pass explicit count and add ranking step.
- `src/agent/tools/llm_molecular_generator.py` — accept structured target-generation input.
- `src/agent/supervisor.py` — register candidate ranker.
- `src/agent/tooling/factory.py` — assign candidate ranker ownership.
- `src/agent/capabilities/catalog.py` — register ranking capability.
- `src/agent/workflows/catalog.py` — allow ranking in target-driven design.
- `tests/test_target_search.py` — retain existing local-search behavior.
- `tests/agent/test_workflow_run_session.py` — terminal partial-message regression.
- `tests/agent/test_task_planner.py` — explicit generation/ranking plan assertions.
- `tests/agent/test_target_driven_design_workflow.py` — end-to-end tool order and Top-N output.

## Task 1: Add the SQLite TTL Cache Contract

**Files:**

- Modify: `src/target_search/database.py`
- Create: `src/target_search/cache.py`
- Test: `tests/test_target_search_fallback.py`

- [ ] **Step 1: Write failing cache schema and TTL tests**

Add tests that initialize a temporary project root and assert:

```python
def test_target_cache_returns_fresh_and_marks_expired_records(tmp_path):
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    cache = TargetCacheRepository(tmp_path, clock=lambda: now)
    cache.put(
        cache_key="target:homo-sapiens:pde5a",
        record_type="target",
        source="UniProt",
        source_record_id="O76074",
        payload={"gene_symbol": "PDE5A", "uniprot_id": "O76074"},
        ttl=timedelta(days=30),
    )

    fresh = cache.get("target:homo-sapiens:pde5a")
    assert fresh is not None
    assert fresh.stale is False
    assert fresh.payload["uniprot_id"] == "O76074"

    cache.clock = lambda: now + timedelta(days=31)
    expired = cache.get("target:homo-sapiens:pde5a", allow_stale=True)
    assert expired is not None
    assert expired.stale is True

    cache.put(
        cache_key="structures:O76074",
        record_type="structures",
        source="RCSB_PDB",
        source_record_id="O76074",
        payload={"structures": [{"structure_id": "1T9R"}]},
        ttl=timedelta(days=7),
    )
    assert cache.get("structures:O76074") is not None
```

Also test that `cleanup_expired()` removes expired rows and refuses to delete a coordinate path outside `get_cache_dir(project_root)`.
Coordinate-file cache rows use `record_type="coordinate_file"` and a payload with
`{"path": relative_path}`; cleanup resolves that path and unlinks it only when it
is a regular file under the configured cache root.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_target_search_fallback.py -q -p no:cacheprovider
```

Expected: collection fails because `src.target_search.cache` does not exist.

- [ ] **Step 3: Add cache tables through idempotent schema migration**

Extend `init_db()` with:

```sql
CREATE TABLE IF NOT EXISTS target_remote_cache (
    cache_key TEXT PRIMARY KEY,
    record_type TEXT NOT NULL,
    source TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_refresh_status TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_target_remote_cache_expiry
    ON target_remote_cache(expires_at);
CREATE TABLE IF NOT EXISTS target_runtime_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

- [ ] **Step 4: Implement the cache repository**

Define immutable `CachedEvidence` and `TargetCacheRepository`:

```python
@dataclass(frozen=True)
class CachedEvidence:
    cache_key: str
    record_type: str
    source: str
    source_record_id: str
    payload: dict[str, Any]
    retrieved_at: datetime
    expires_at: datetime
    stale: bool

class TargetCacheRepository:
    def __init__(self, project_root=None, clock=None):
        self.project_root = resolve_project_root(project_root)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        init_db(self.project_root)

    def put(self, *, cache_key, record_type, source, source_record_id, payload, ttl):
        now = self.clock()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        expires_at = now + ttl
        with get_connection(self.project_root) as conn:
            conn.execute(
                """INSERT INTO target_remote_cache
                   (cache_key, record_type, source, source_record_id, payload_json,
                    payload_digest, retrieved_at, expires_at, last_refresh_status,
                    schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ok', 1)
                   ON CONFLICT(cache_key) DO UPDATE SET
                     record_type=excluded.record_type,
                     source=excluded.source,
                     source_record_id=excluded.source_record_id,
                     payload_json=excluded.payload_json,
                     payload_digest=excluded.payload_digest,
                     retrieved_at=excluded.retrieved_at,
                     expires_at=excluded.expires_at,
                     last_refresh_status='ok',
                     schema_version=excluded.schema_version""",
                (cache_key, record_type, source, source_record_id, serialized,
                 digest, now.isoformat(), expires_at.isoformat()),
            )

    def get(self, cache_key, *, allow_stale=False):
        with get_connection(self.project_root) as conn:
            row = conn.execute(
                "SELECT * FROM target_remote_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        stale = expires_at <= self.clock()
        if stale and not allow_stale:
            return None
        return CachedEvidence(
            cache_key=row["cache_key"], record_type=row["record_type"],
            source=row["source"], source_record_id=row["source_record_id"],
            payload=json.loads(row["payload_json"]),
            retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
            expires_at=expires_at, stale=stale,
        )

    def mark_refresh_failure(self, cache_key, reason):
        status = str(reason).replace("\r", " ").replace("\n", " ")[:500]
        with get_connection(self.project_root) as conn:
            conn.execute(
                "UPDATE target_remote_cache SET last_refresh_status = ? WHERE cache_key = ?",
                (status, cache_key),
            )

    def cleanup_expired(self, *, limit=100):
        now = self.clock().isoformat()
        with get_connection(self.project_root) as conn:
            rows = conn.execute(
                "SELECT cache_key, payload_json FROM target_remote_cache "
                "WHERE expires_at <= ? ORDER BY expires_at LIMIT ?",
                (now, max(0, int(limit))),
            ).fetchall()
            conn.executemany(
                "DELETE FROM target_remote_cache WHERE cache_key = ?",
                [(row["cache_key"],) for row in rows],
            )
        cache_root = get_cache_dir(self.project_root).resolve()
        for row in rows:
            payload = json.loads(row["payload_json"])
            path_value = payload.get("path") if isinstance(payload, dict) else None
            if path_value:
                candidate = absolute_from_project(path_value, self.project_root).resolve()
                if candidate.is_relative_to(cache_root) and candidate.is_file():
                    candidate.unlink()
        return len(rows)
```

Use canonical UTC ISO-8601 timestamps and cap refresh status at 500 characters.

- [ ] **Step 5: Run cache tests and verify GREEN**

Run the command from Step 2. Expected: cache tests pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add src/target_search/database.py src/target_search/cache.py tests/test_target_search_fallback.py
git commit -m "feat: add expiring target evidence cache"
```

## Task 2: Add Authoritative Source Clients

**Files:**

- Create: `src/target_search/remote_clients.py`
- Test: `tests/test_target_search_fallback.py`

- [ ] **Step 1: Write failing client contract tests**

Use an injected fake `requests.Session` and assert exact request construction and normalized outputs:

```python
def test_uniprot_client_resolves_exact_human_gene(fake_session):
    fake_session.enqueue_json(UNIPROT_PDE5A_RESPONSE)
    record = UniProtClient(session=fake_session).resolve("PDE5A", "Homo sapiens")
    assert record["gene_symbol"] == "PDE5A"
    assert record["uniprot_id"] == "O76074"
    assert record["source"] == "UniProt"
    assert record["source_url"].startswith("https://rest.uniprot.org/")

def test_rcsb_client_uses_uniprot_accession_and_bounds_results(fake_session):
    fake_session.enqueue_json(RCSB_SEARCH_RESPONSE)
    fake_session.enqueue_json(RCSB_ENTRY_RESPONSE)
    structures = RcsbClient(session=fake_session, max_structures=3).search("O76074")
    assert len(structures) <= 3
    assert structures[0]["structure_type"] == "experimental"
    assert isinstance(structures[0]["resolution"], float)

def test_alphafold_client_marks_prediction_as_non_experimental(fake_session):
    fake_session.enqueue_json(ALPHAFOLD_PDE5A_RESPONSE)
    structure = AlphaFoldClient(session=fake_session).lookup("O76074")
    assert structure["source"] == "AlphaFold"
    assert structure["structure_type"] == "predicted"
    assert structure["resolution"] is None
```

Add explicit tests for 404, malformed JSON, timeout, 429 with `Retry-After`, response-size rejection, and exhausted retries. Assert errors contain source/status but not full response bodies.

- [ ] **Step 2: Run client tests and verify RED**

Expected: import failure for `src.target_search.remote_clients`.

- [ ] **Step 3: Implement a shared bounded HTTP helper**

Implement:

```python
@dataclass(frozen=True)
class RemoteSourceError(RuntimeError):
    source: str
    code: str
    retryable: bool
    status_code: int | None = None

class BoundedJsonClient:
    def _request_json(self, method, url, **kwargs):
        for attempt in range(self.max_attempts):
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=(self.connect_timeout, self.read_timeout),
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                    **kwargs,
                )
            except requests.Timeout as exc:
                if attempt + 1 == self.max_attempts:
                    raise RemoteSourceError(
                        self.source, "timeout", True
                    ) from exc
                self.sleep(min(self.backoff_base * (2 ** attempt), self.backoff_max))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 == self.max_attempts:
                    raise RemoteSourceError(
                        self.source,
                        "rate_limited" if response.status_code == 429 else "provider_error",
                        True,
                        response.status_code,
                    )
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else self.backoff_base * (2 ** attempt)
                self.sleep(min(delay, self.backoff_max))
                continue
            if response.status_code == 404:
                return None
            response.raise_for_status()
            if len(response.content) > self.max_response_bytes:
                raise RemoteSourceError(self.source, "response_too_large", False)
            return response.json()
```

Never include authorization headers, query secrets, or response bodies in raised messages.

- [ ] **Step 4: Implement the three official-source clients**

Use these endpoints:

```python
UNIPROT_SEARCH = "https://rest.uniprot.org/uniprotkb/search"
RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
RCSB_ENTRY = "https://data.rcsb.org/rest/v1/core/entry/{entry_id}"
ALPHAFOLD_PREDICTION = "https://alphafold.ebi.ac.uk/api/prediction/{accession}"
```

UniProt requests exact gene/accession terms and organism ID 9606 for default human searches. RCSB searches reference-sequence accession `O76074`, returns entry IDs, then reads bounded metadata. AlphaFold accepts only the resolved accession, not arbitrary user text.

- [ ] **Step 5: Run client tests and verify GREEN**

Run only the new client tests first, then all of `tests/test_target_search_fallback.py`.

- [ ] **Step 6: Commit Task 2**

```powershell
git add src/target_search/remote_clients.py tests/test_target_search_fallback.py
git commit -m "feat: add authoritative target source clients"
```

## Task 3: Orchestrate Seed, Cache, and Remote Fallback

**Files:**

- Create: `src/target_search/authoritative_resolver.py`
- Modify: `src/target_search/service.py`
- Modify: `src/agent/tools/target_database_tool.py`
- Test: `tests/test_target_search_fallback.py`
- Test: `tests/test_target_search.py`

- [ ] **Step 1: Write failing resolver and service tests**

Cover the ordered chain:

```python
def test_local_hit_never_calls_remote(tmp_path, seeded_project, resolver_spy):
    service = TargetSearchService(seeded_project, resolver=resolver_spy)
    result = service.search_targets("PDE5A")
    assert result["results"][0]["gene_symbol"] == "PDE5A"
    assert result["lookup_path"] == ["local"]
    assert resolver_spy.calls == []

def test_empty_database_seeds_once_then_retries(tmp_path, seed_spy):
    service = TargetSearchService(tmp_path, resolver=FailIfCalledResolver())
    first = service.search_targets("PDE5A")
    second = service.search_targets("PDE5A")
    assert first["results"][0]["gene_symbol"] == "PDE5A"
    assert second["results"][0]["gene_symbol"] == "PDE5A"
    assert seed_spy.call_count == 1

def test_local_miss_uses_remote_and_writes_cache(tmp_path, resolver):
    service = TargetSearchService(tmp_path, resolver=resolver, auto_seed=False)
    result = service.search_targets("PDE5A")
    assert result["lookup_path"] == ["local", "cache", "UniProt", "RCSB_PDB"]
    assert result["results"][0]["recommended_structures"]
    assert TargetCacheRepository(tmp_path).get(
        "target:homo-sapiens:pde5a"
    ) is not None
    assert TargetCacheRepository(tmp_path).get("structures:O76074") is not None
```

Also test fresh cache avoids remote calls, stale cache is used only after retryable remote failure, permanent malformed evidence is not cached, and ambiguous UniProt results return `status="ambiguous"`.

- [ ] **Step 2: Run service tests and verify RED**

Expected: constructor does not accept resolver/auto-seed and no fallback exists.

- [ ] **Step 3: Implement `AuthoritativeTargetResolver`**

Its result contract is:

```python
@dataclass(frozen=True)
class TargetResolution:
    status: Literal["resolved", "not_found", "ambiguous", "unavailable"]
    target: dict[str, Any] | None
    structures: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    lookup_path: tuple[str, ...]

def resolve(self, query: str, organism: str) -> TargetResolution:
    target = self.uniprot.resolve(query, organism)
    if target is None:
        return TargetResolution("not_found", None, (), (), ("UniProt",))
    structures = self.rcsb.search(target["uniprot_id"])
    if not structures:
        prediction = self.alphafold.lookup(target["uniprot_id"])
        structures = [prediction] if prediction else []
    return TargetResolution(
        "resolved",
        target,
        tuple(structures),
        (),
        ("UniProt", "RCSB_PDB" if structures and structures[0]["source"] == "RCSB_PDB" else "AlphaFold"),
    )
```

Normalize target records with source, source ID/URL, retrieval/expiry timestamps, stale flag, match reason, structure counts, and `recommended_structures`.

- [ ] **Step 4: Implement service orchestration**

Refactor the existing SQL into `_search_local()`. The public method becomes:

```python
def search_targets(
    self,
    query,
    target_type=None,
    source=None,
    has_experimental=None,
    docking_recommended=None,
    has_ligand=None,
    organism=None,
):
    filters = {
        "target_type": target_type,
        "source": source,
        "has_experimental": has_experimental,
        "docking_recommended": docking_recommended,
        "has_ligand": has_ligand,
    }
    local = self._search_local(query, filters)
    if local:
        return self._payload(query, local, ["local"])
    if self.auto_seed and self._target_count() == 0 and self._seed_files_exist():
        self._seed_once()
        local = self._search_local(query, filters)
        if local:
            return self._payload(query, local, ["local", "seed"])
    target_key = self._target_cache_key(query, organism)
    cached_target = self.cache.get(target_key)
    if cached_target:
        accession = cached_target.payload["uniprot_id"]
        cached_structures = self.cache.get(f"structures:{accession}")
        if cached_structures:
            return self._cached_payload(query, cached_target, cached_structures)
    resolution = self.resolver.resolve(query, organism or self.default_organism)
    if resolution.status == "resolved":
        payload = self._resolution_payload(query, resolution, filters)
        self.cache.put(
            cache_key=target_key,
            record_type="target",
            source="UniProt",
            source_record_id=payload["results"][0]["uniprot_id"],
            payload=payload["results"][0],
            ttl=timedelta(days=30),
        )
        self.cache.put(
            cache_key=f"structures:{payload['results'][0]['uniprot_id']}",
            record_type="structures",
            source="RCSB_PDB" if any(
                item["source"] == "RCSB_PDB"
                for item in payload["results"][0]["recommended_structures"]
            ) else "AlphaFold",
            source_record_id=payload["results"][0]["uniprot_id"],
            payload={"structures": payload["results"][0]["recommended_structures"]},
            ttl=timedelta(days=7),
        )
        return payload
    stale_target = self.cache.get(target_key, allow_stale=True)
    stale_structures = (
        self.cache.get(
            f"structures:{stale_target.payload['uniprot_id']}", allow_stale=True
        )
        if stale_target is not None
        else None
    )
    if resolution.status == "unavailable" and stale_target is not None:
        return self._cached_payload(
            query, stale_target, stale_structures, warnings=resolution.warnings
        )
    return self._resolution_payload(query, resolution, filters)
```

Use a module-level lock for same-process seed initialization and rely on idempotent SQL constraints for cross-process races. Never rebuild or delete an existing database during request handling.

- [ ] **Step 5: Preserve remote structures and provenance in the Agent tool**

In `TargetDatabaseTool.execute()`:

- aggregate `warnings` and `lookup_path` from each service call;
- keep an existing `recommended_structures` list when a remote record has no local `target_id`;
- add evidence records containing source, source URL, source ID, retrieved timestamp, expiry, and stale state;
- describe zero matches as “authoritative sources returned no evidence” rather than “database empty”.

- [ ] **Step 6: Run local and fallback tests and verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_target_search_fallback.py tests\test_target_search.py -q -p no:cacheprovider
```

- [ ] **Step 7: Commit Task 3**

```powershell
git add src/target_search/authoritative_resolver.py src/target_search/service.py src/agent/tools/target_database_tool.py tests/test_target_search_fallback.py tests/test_target_search.py
git commit -m "feat: add authoritative target lookup fallback"
```

## Task 4: Fix Partial Terminal Event Semantics

**Files:**

- Modify: `src/agent/runtime/run_session.py`
- Test: `tests/agent/test_workflow_run_session.py`
- Test: `tests/home_workflow_completion_behavior_test.js`

- [ ] **Step 1: Write a failing terminal-message regression test**

Build a two-step workflow where target search succeeds with no records and generation is blocked by `target_evidence`:

```python
result = session.run_to_completion()
terminal = events[-1]
assert result.outcome == RunOutcome.PARTIAL
assert terminal.event == TaskEventType.TASK_PARTIAL
assert "partial" in terminal.message.lower()
assert terminal.message != "Workflow completed"
assert terminal.progress == 1.0
```

The Node test must assert terminal progress 100% does not apply success styling when the event type is `task_partial`.

- [ ] **Step 2: Run tests and verify RED**

Expected: event type is partial but message remains `Workflow completed`.

- [ ] **Step 3: Return the post-validation final message**

Change `_build_final_result()` so the return value is:

```python
return agent_result, agent_result.message
```

Ensure runtime failures still replace both message and final answer. Do not redefine 100% progress; it continues to mean terminal execution.

- [ ] **Step 4: Run Python and Node tests and verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_workflow_run_session.py -q -p no:cacheprovider
node tests\home_workflow_completion_behavior_test.js
```

- [ ] **Step 5: Commit Task 4**

```powershell
git add src/agent/runtime/run_session.py tests/agent/test_workflow_run_session.py tests/home_workflow_completion_behavior_test.js
git commit -m "fix: report blocked workflows as partial"
```

## Task 5: Carry Explicit Generation Count Through Workflow Bindings

**Files:**

- Modify: `src/agent/planning/bindings.py`
- Modify: `src/agent/planning/task_planner.py`
- Modify: `src/agent/tools/llm_molecular_generator.py`
- Test: `tests/agent/test_binding_resolver.py`
- Test: `tests/agent/test_task_planner.py`
- Test: `tests/test_llm_molecular_generator.py`

- [ ] **Step 1: Write failing structured-binding tests**

Specify a new selector that carries request metadata and all outputs:

```python
payload = resolver.resolve(
    "$.workflow",
    "identity",
    request={"query": "设计 10 个", "metadata": {"requested_count": 10}},
    outputs={"target": [{"gene_symbol": "PDE5A", "source": "UniProt"}]},
)
assert payload["query"] == "设计 10 个"
assert payload["metadata"]["requested_count"] == 10
assert payload["outputs"]["target"][0]["gene_symbol"] == "PDE5A"
```

Test that generator structured input overrides text parsing with `requested_count=10` and includes target evidence in the generation prompt.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: `$.workflow` is unsupported and generator expects a string.

- [ ] **Step 3: Add the structured workflow binding**

In `BindingResolver.resolve()`:

```python
if selector == "$.workflow":
    value = {
        "query": request.get("query"),
        "metadata": dict(request.get("metadata") or {}),
        "outputs": dict(outputs),
    }
```

Continue rejecting every other unknown selector.

- [ ] **Step 4: Bind generation to explicit workflow input**

Set the generation step to `input_binding="$.workflow"` and remove the lossy text template. Add a generator helper:

```python
def _normalize_request(self, value):
    if isinstance(value, Mapping):
        query = str(value.get("query") or "")
        metadata = dict(value.get("metadata") or {})
        target_evidence = dict(value.get("outputs") or {}).get("target")
        return query, int(metadata.get("requested_count") or 1), target_evidence
    return str(value), None, None
```

Pass the explicit count to `_analyze_generation_intent()` and include serialized, bounded target evidence in the Ollama prompt. Preserve legacy string inputs.

- [ ] **Step 5: Run focused tests and verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_binding_resolver.py tests\agent\test_task_planner.py tests\test_llm_molecular_generator.py -q -p no:cacheprovider
```

- [ ] **Step 6: Commit Task 5**

```powershell
git add src/agent/planning/bindings.py src/agent/planning/task_planner.py src/agent/tools/llm_molecular_generator.py tests/agent/test_binding_resolver.py tests/agent/test_task_planner.py tests/test_llm_molecular_generator.py
git commit -m "feat: bind explicit generation requests"
```

## Task 6: Implement Deterministic Top-N Candidate Ranking

**Files:**

- Create: `src/agent/tools/candidate_ranker.py`
- Modify: `src/agent/planning/task_planner.py`
- Modify: `src/agent/supervisor.py`
- Modify: `src/agent/tooling/factory.py`
- Modify: `src/agent/capabilities/catalog.py`
- Modify: `src/agent/workflows/catalog.py`
- Test: `tests/agent/test_candidate_ranker.py`
- Test: `tests/agent/test_target_driven_design_workflow.py`

- [ ] **Step 1: Write failing ranking tests**

Provide three aligned candidates and verify deterministic ranking:

```python
payload = {
    "metadata": {"docking_top_n": 2},
    "outputs": {
        "molecules": [{"smiles": "CCO"}, {"smiles": "CCN"}, {"smiles": "CCC"}],
        "properties": [
            {"smiles": "CCO", "properties": {"qed": 0.72, "logp": 0.1}},
            {"smiles": "CCN", "properties": {"qed": 0.65, "logp": 0.0}},
            {"smiles": "CCC", "properties": {"qed": 0.40, "logp": 1.4}},
        ],
        "admet": [],
        "activity": [],
    },
}
result = CandidateRanker().execute(payload)
assert result["success"] is True
assert len(result["data"]["top_candidates"]) == 2
assert all("ranking_evidence" in item for item in result["data"]["top_candidates"])
assert "binding_energy" not in json.dumps(result)
assert "kcal/mol" not in json.dumps(result)
```

Add tests that reject candidates missing from the validated molecule set, ignore demo/fallback activity values, produce warnings for absent optional assessments, and use canonical SMILES for joins.

- [ ] **Step 2: Run ranking tests and verify RED**

Expected: `CandidateRanker` does not exist.

- [ ] **Step 3: Implement evidence-aware ranking**

Ranking rules:

```python
property_score = 0.70 * clamp(qed, 0.0, 1.0) + 0.30 * logp_window_score(logp)
admet_score = 1.0 - min(validated_risk_count / max(total_endpoints, 1), 1.0)
activity_score = normalized_real_activity if real_activity_available else None
available = [(property_score, 0.50), (admet_score, 0.25), (activity_score, 0.25)]
score = sum(value * weight for value, weight in available if value is not None) / sum(
    weight for value, weight in available if value is not None
)
```

`logp_window_score` is 1.0 for 1–3 and decreases linearly to zero at -1 and 5. A candidate without real property evidence is not rankable. Tie-break by canonical SMILES for reproducibility. Output `score`, `rank`, `ranking_evidence`, `missing_evidence`, and `docking_ready_for_preparation`; never output docking energy.

- [ ] **Step 4: Register and plan the ranking step**

Add capability `candidate.rank`, tool ownership `molecular_design`, and workflow permission `candidate_ranker`. Append:

```python
WorkflowStep(
    "candidate_ranking",
    "candidate_ranker",
    input_binding="$.workflow",
    input_transform="identity",
    output_key="ranking",
    capability="candidate.rank",
    output_contract="CandidateRanking@1",
)
```

The ranking step is required after properties; ADMET/activity remain optional. Because sequential order currently places ranking after both optional steps, it receives every successful output available in `$.workflow`.

- [ ] **Step 5: Run ranking and workflow tests and verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_candidate_ranker.py tests\agent\test_target_driven_design_workflow.py tests\agent\test_task_planner.py -q -p no:cacheprovider
```

- [ ] **Step 6: Commit Task 6**

```powershell
git add src/agent/tools/candidate_ranker.py src/agent/planning/task_planner.py src/agent/supervisor.py src/agent/tooling/factory.py src/agent/capabilities/catalog.py src/agent/workflows/catalog.py tests/agent/test_candidate_ranker.py tests/agent/test_target_driven_design_workflow.py tests/agent/test_task_planner.py
git commit -m "feat: rank target-driven candidates from evidence"
```

## Task 7: Add Cache Cleanup and Operational Visibility

**Files:**

- Create: `scripts/cleanup_target_cache.py`
- Modify: `src/target_search/service.py`
- Modify: `src/target_search/downloader.py`
- Test: `tests/test_target_search_fallback.py`

- [ ] **Step 1: Write failing cleanup and health tests**

Assert that health output contains only operational fields:

```python
health = service.get_fallback_health()
assert set(health) == {
    "local_target_count",
    "local_structure_count",
    "seed_available",
    "cache_entry_count",
    "stale_entry_count",
    "last_refresh_errors",
}
assert "db_path" not in health
```

Test the CLI with a temporary configured DB/cache and verify `--limit 10` removes no more than ten expired records.

- [ ] **Step 2: Run tests and verify RED**

Expected: health method and cleanup CLI are absent.

- [ ] **Step 3: Implement bounded cleanup and health**

The script parses only `--limit` and calls `TargetCacheRepository.cleanup_expired(limit=...)`. It prints counts, not cached payloads or absolute paths. Service initialization may opportunistically run one cleanup batch, but cleanup failure becomes a warning and cannot prevent search. After `StructureDownloader` atomically completes a coordinate download, it registers a `coordinate_file` cache row with a 90-day TTL; failed or partial downloads are not registered.

- [ ] **Step 4: Run cleanup tests and verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_target_search_fallback.py -q -p no:cacheprovider
```

- [ ] **Step 5: Commit Task 7**

```powershell
git add scripts/cleanup_target_cache.py src/target_search/service.py src/target_search/downloader.py tests/test_target_search_fallback.py
git commit -m "feat: manage target fallback cache lifecycle"
```

## Task 8: Real PDE5A Acceptance and Full Regression

**Files:**

- Modify: `tests/test_target_search_fallback.py`
- Modify: `tests/agent/test_real_acceptance_checks.py`
- Modify: `data/agent_evals/golden_scientific_cases.jsonl` only if the existing PDE5A case lacks the new source/cache assertions.

- [ ] **Step 1: Add an opt-in real-network PDE5A test**

Mark the test with `pytest.mark.real_external` and skip unless `RUN_REAL_TARGET_SEARCH=1`:

```python
@pytest.mark.real_external
def test_real_pde5a_authoritative_lookup(tmp_path, monkeypatch):
    if os.getenv("RUN_REAL_TARGET_SEARCH") != "1":
        pytest.skip("real authoritative target lookup not enabled")
    service = TargetSearchService(tmp_path, auto_seed=False)
    result = service.search_targets("PDE5A")
    target = result["results"][0]
    assert target["uniprot_id"] == "O76074"
    assert target["source"] == "UniProt"
    assert target["recommended_structures"]
    assert target["recommended_structures"][0]["source"] in {"RCSB_PDB", "AlphaFold"}
```

The test must fail or skip honestly on network/source failure; it must not substitute fixtures.

- [ ] **Step 2: Run all focused tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_target_search_fallback.py tests\test_target_search.py tests\agent\test_binding_resolver.py tests\agent\test_task_planner.py tests\agent\test_candidate_ranker.py tests\agent\test_target_driven_design_workflow.py tests\agent\test_workflow_run_session.py -q -p no:cacheprovider
```

Expected: all deterministic tests pass.

- [ ] **Step 3: Run real PDE5A source acceptance**

```powershell
$env:RUN_REAL_TARGET_SEARCH = "1"
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_target_search_fallback.py -m real_external -q -p no:cacheprovider
Remove-Item Env:RUN_REAL_TARGET_SEARCH
```

Expected: PDE5A resolves to UniProt `O76074` and returns at least one source-backed structure, or fails with the exact external dependency reason.

- [ ] **Step 4: Run Agent and safety regressions**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py tests\agent\test_real_acceptance_checks.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
node tests\home_workflow_completion_behavior_test.js
```

- [ ] **Step 5: Run contract acceptance**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract
```

Expected: contract status passed. Any unavailable real dependency remains partial/failed in real mode.

- [ ] **Step 6: Verify repository boundaries**

```powershell
git status --short
git diff --check
git diff --name-only HEAD~7..HEAD
```

Confirm the existing `data/molecular_faiss_index.index.manifest.json` remains untracked and untouched, no API key appears in changed files, and only this plan’s files are committed.

- [ ] **Step 7: Commit acceptance updates**

```powershell
git add tests/test_target_search_fallback.py tests/agent/test_real_acceptance_checks.py data/agent_evals/golden_scientific_cases.jsonl
git commit -m "test: verify authoritative target fallback"
```

Do not add the JSONL file if no case modification was necessary.

## Completion Criteria

- Local target hits remain offline and backward compatible.
- Empty local SQLite initializes from repository seeds without rebuilding or deleting user data.
- Missing local targets resolve through UniProt and RCSB/AlphaFold with bounded, source-specific failures.
- Remote evidence is cached in SQLite and obeys 30-day target, 7-day structure, and 90-day coordinate-file lifetimes.
- Stale evidence is used only after retryable source failure and is labeled in warnings/provenance.
- A blocked scientific precondition emits a partial/failed message, never stale `Workflow completed` text.
- Requested count is explicitly bound into the local Ollama generator.
- Top-N ranking consumes generated candidates and real downstream assessments, produces no docking energy, and remains deterministic.
- Real PDE5A acceptance proves the external chain when network access is available.
- No unrelated worktree item or secret is staged or committed.
