"""Synthetic contracts for exact structure selection; not scientific evidence."""
import pytest

from src.target_search.authoritative_resolver import AuthoritativeTargetResolver, TargetResolution
from src.target_search.service import TargetSearchService
from tests.test_target_search_fallback import _authoritative_target, _experimental_structure


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TARGET_DB_PATH", str(tmp_path / "targets.sqlite"))
    monkeypatch.setenv("TARGET_CACHE_DIR", str(tmp_path / "cache"))
    import requests
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send",
                        lambda *a, **k: pytest.fail("unexpected network"))


def target():
    value = _authoritative_target()
    value.update(gene_symbol="BCHE", uniprot_id="P06276", source_record_id="P06276",
                 source_url="https://www.uniprot.org/uniprotkb/P06276/entry")
    return value


class Resolver:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def resolve(self, query, organism, **kwargs):
        self.calls.append((query, organism, kwargs))
        if self.result is not None:
            return self.result
        return TargetResolution(status="resolved", target=target(),
            structures=(_experimental_structure(kwargs.get("pdb_id", "1P0I")),),
            lookup_path=("UniProt", "RCSB_PDB"))


def service(tmp_path, resolver):
    return TargetSearchService(tmp_path, resolver=resolver, auto_seed=False)


def ids(payload):
    return [s["structure_id"] for t in payload["results"] for s in t["recommended_structures"]]


def test_exact_bypasses_local_and_isolates_generic_cache(tmp_path, monkeypatch):
    resolver = Resolver()
    s = service(tmp_path, resolver)
    assert ids(s.search_targets("P06276")) == ["1P0I"]
    generic = s.cache.get(s._structures_cache_key("P06276"))
    exact = s.search_targets("P06276", pdb_id="6sam")
    assert ids(exact) == ["6SAM"]
    assert resolver.calls[-1] == ("P06276", "Homo sapiens", {"pdb_id": "6SAM"})
    assert ids(s.search_targets("P06276")) == ["1P0I"]
    assert s.cache.get(s._structures_cache_key("P06276")).generation_id == generic.generation_id
    count = len(resolver.calls)
    monkeypatch.setattr(s, "_search_local", lambda *a, **k: pytest.fail("exact must bypass local"))
    assert ids(s.search_targets("P06276", pdb_id="6SAM")) == ["6SAM"]
    assert len(resolver.calls) == count


@pytest.mark.parametrize("value", [True, 6, "", "6SAMX", "../6SAM", "https://www.rcsb.org/structure/6SAM"])
def test_invalid_exact_id_never_falls_back(tmp_path, value):
    resolver = Resolver(); s = service(tmp_path, resolver)
    r = s.search_targets("P06276", pdb_id=value)
    assert r["status"] in {"invalid_input", "unavailable"}
    assert not r["results"] and not resolver.calls


@pytest.mark.parametrize("mutation", ["entry", "organism", "duplicate", "empty", "method"])
def test_exact_results_must_match_identity(tmp_path, mutation):
    rows = [_experimental_structure("6SAM")]
    if mutation == "entry": rows[0] = _experimental_structure("1P0I")
    elif mutation == "organism": rows[0]["organism"] = "Mus musculus"
    elif mutation == "duplicate": rows *= 2
    elif mutation == "method": rows[0]["method"] = "UNKNOWN METHOD"
    else: rows = []
    resolver = Resolver(TargetResolution(status="resolved", target=target(), structures=tuple(rows)))
    r = service(tmp_path, resolver).search_targets("P06276", pdb_id="6SAM")
    assert not r["results"] and r["status"] != "resolved"


def test_exact_cache_revalidates_entry(tmp_path):
    resolver = Resolver(); s = service(tmp_path, resolver)
    assert ids(s.search_targets("P06276", pdb_id="6SAM")) == ["6SAM"]
    key = s._structures_cache_key("P06276", pdb_id="6SAM", organism="Homo sapiens")
    payload = s.cache.get(key).to_dict()["payload"]
    # A correctly digested, but semantically wrong cache record is not authority.
    payload["structures"] = s._normalize_structure_evidence((_experimental_structure("1P0I"),),
        accession="P06276", organism="Homo sapiens")
    s.cache.upsert(key, "structures", "RCSB_PDB", "P06276", payload)
    assert ids(s.search_targets("P06276", pdb_id="6SAM")) == ["6SAM"]
    assert len(resolver.calls) == 2


def test_exact_cache_revalidates_experimental_method(tmp_path):
    resolver = Resolver(); s = service(tmp_path, resolver)
    assert ids(s.search_targets("P06276", pdb_id="6SAM")) == ["6SAM"]
    key = s._structures_cache_key("P06276", pdb_id="6SAM", organism="Homo sapiens")
    payload = s.cache.get(key).to_dict()["payload"]
    payload["structures"][0]["method"] = "UNKNOWN METHOD"
    s.cache.upsert(key, "structures", "RCSB_PDB", "P06276", payload)
    r = s.search_targets("P06276", pdb_id="6SAM")
    assert r["results"][0]["recommended_structures"][0]["method"] == "X-RAY DIFFRACTION"
    assert len(resolver.calls) == 2


def test_exact_cache_cannot_rebind_requested_accession(tmp_path):
    resolver = Resolver(); s = service(tmp_path, resolver)
    wrong_target = s._normalize_target_evidence(_authoritative_target())
    s.cache.upsert(s._target_cache_key("P06276"), "target", "UniProt", "P00533", wrong_target)
    r = s.search_targets("P06276", pdb_id="6SAM")
    assert resolver.calls[-1][0] == "P06276"
    assert r["results"][0]["uniprot_id"] == "P06276"


def test_exact_provider_failure_does_not_return_stale_broad_results(tmp_path):
    resolver = Resolver(); s = service(tmp_path, resolver)
    assert ids(s.search_targets("P06276")) == ["1P0I"]
    resolver.result = TargetResolution(status="unavailable", retryable=True,
        source="RCSB_PDB", code="request_failure")
    r = s.search_targets("P06276", pdb_id="6SAM")
    assert r["status"] == "unavailable" and not r["results"]


def test_resolver_exact_miss_never_uses_alphafold():
    class UniProt:
        def resolve(self, query, organism): return target()
    class Rcsb:
        def search(self, accession, **kw):
            assert accession == "P06276" and kw == {"pdb_id": "6SAM"}
            return []
    class AlphaFold:
        def lookup(self, accession): pytest.fail("exact PDB miss cannot become AlphaFold")
    r = AuthoritativeTargetResolver(UniProt(), Rcsb(), AlphaFold()).resolve("P06276", pdb_id="6SAM")
    assert r.status == "not_found" and not r.structures


@pytest.mark.parametrize("query", ["查找 UniProt P06276，PDB: 6sam", {"uniprot_id": "P06276", "pdb_id": "6SAM"}])
def test_tool_retains_explicit_pdb(tmp_path, query):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    resolver = Resolver(); tool = TargetDatabaseTool(); tool._service = service(tmp_path, resolver)
    r = tool.execute(query)
    assert r["success"], r
    assert resolver.calls[-1][0] == "P06276"
    assert resolver.calls[-1][2] == {"pdb_id": "6SAM"}
    assert r["data"][0]["recommended_structures"][0]["structure_id"] == "6SAM"


@pytest.mark.parametrize("query", [
    "查找 UniProt P06276 PDB: 6SAMX", "查找 UniProt P06276 PDB: ../6SAM",
    "查找 UniProt P06276 PDB: 6SAM PDB: 1P0I", "不要查找 UniProt P06276 PDB: 6SAM",
    {"uniprot_id": "P06276", "pdb_id": None},
    "UniProt P06276, PDB: 6SAM, 1P0I",
    "UniProt P06276, PDB: 6SAM and 1P0I",
    "UniProt P06276, PDB: 6SAM、1P0I",
    "UniProt P06276, PDB: 6SAM；1P0I",
    "UniProt P06276, PDB: 6SAM 或 1P0I",
    "Find BCHE, PDB: 6SAM, or 1P0I",
    "Find BCHE, PDB: 6SAM 或者 1P0I",
    "Find BCHE, PDB: 6SAM，或者 1P0I",
])
def test_bad_explicit_pdb_is_not_silently_dropped(tmp_path, query):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    resolver = Resolver(); tool = TargetDatabaseTool(); tool._service = service(tmp_path, resolver)
    r = tool.execute(query)
    assert r["success"] is False and r["error"]["code"] == "validation_error"
    assert not resolver.calls


def test_exact_gene_phrase_uses_known_target_hint(tmp_path):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    resolver = Resolver(); tool = TargetDatabaseTool(); tool._service = service(tmp_path, resolver)
    r = tool.execute("查找 BCHE 的实验结构，PDB: 6SAM")
    assert r["success"], r
    assert resolver.calls[-1][0] == "BCHE"


def test_structured_exact_request_cannot_drop_conflicting_target(tmp_path):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    resolver = Resolver(); tool = TargetDatabaseTool(); tool._service = service(tmp_path, resolver)
    r = tool.execute({"gene_symbol": "EGFR", "uniprot_id": "P06276", "pdb_id": "6SAM"})
    assert r["success"] is False and r["error"]["code"] == "validation_error"
    assert not resolver.calls


@pytest.mark.parametrize("name,success", [("EGFR", False), ("BCHE", True), ("BuChE", True)])
def test_labelled_accession_cannot_override_named_exact_target(tmp_path, name, success):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    resolver = Resolver(); tool = TargetDatabaseTool(); tool._service = service(tmp_path, resolver)
    r = tool.execute(f"Find {name}; UniProt P06276, PDB: 6SAM")
    assert r["success"] is success
    if not success:
        assert r["error"]["code"] == "validation_error"
        assert not r.get("data")


@pytest.mark.parametrize("query", ["查找 BCHE 的实验结构，PDB: 6SAM", "查找人源 BuChE，UniProt P06276，PDB: 6SAM 的实验结构"])
def test_planner_keeps_original_exact_constraint(query):
    from src.agent.contracts import AgentContext
    from src.agent.planning.task_planner import TaskPlanner
    p = TaskPlanner().plan(AgentContext(query=query, trace_id="test", active_skill="target_database_search"))
    assert p.steps[0].input_data == query
