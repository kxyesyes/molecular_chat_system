from dataclasses import replace

import pytest

from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
from src.agent.tools.property_calculator import PropertyCalculator


@pytest.fixture
def tools():
    from src.web.goal_tools import GoalTools
    registry = build_tool_registry([PropertyCalculator(), DrugLikenessAssessment()])
    try:
        yield GoalTools(registry)
    finally:
        registry.close()


def test_bound_molecular_action_uses_complete_original_fields_and_exact_batch_identity(tools):
    source = ["给乙醇（SMILES: CCO）和 CCN 做性质分析。"]
    values = ["CCO", "CCN"]
    def bind(values):
        return tools.bind("property_calculator", {"smiles": values}, user_texts=source)
    step = bind(values)
    values.reverse()
    assert step.arguments == {"smiles": ["CCO", "CCN"]}
    assert step.key != bind(values).key
    assert step.key == bind(["CCO", "CCN"]).key
    assert step.key != tools.bind("drug_likeness_assessment", step.arguments, user_texts=source).key
    with pytest.raises(ValueError):
        bind(["CO"])
    adapter = tools.registry.resolve("property_calculator")
    adapter.spec = replace(adapter.spec, version="2")
    assert bind(["CCO", "CCN"]).key != step.key
    assert not tools.execute(step)["ok"]


@pytest.mark.parametrize("source", ["CCO_suffix", "CCO?broken", "CCO.CCN.", "(CCO)broken", "https://example.org/CCO"])
def test_does_not_bind_partial_molecule_or_url(tools, source):
    with pytest.raises(ValueError):
        tools.bind("property_calculator", {"smiles": ["CCO"]}, user_texts=[source])


def test_actual_readonly_adapters_return_verified_values_and_identities(tools):
    args = {"smiles": ["CCN", "CCO"]}
    for tool in ("property_calculator", "drug_likeness_assessment"):
        step = tools.bind(tool, args, user_texts=["比较 CCN 和 CCO。"])
        outcome = tools.execute(step)
        assert outcome["ok"], outcome
        assert [row["smiles"] for row in outcome["facts"]] == args["smiles"]
        assert outcome["tool_name"] == tool and outcome["tool_version"] == step.version
        assert outcome["provenance"]["input_digest"] == step.key
        assert len(outcome["provenance"]["output_digest"]) == 64
        if tool == "property_calculator":
            assert outcome["facts"][1]["molecular_weight"] == pytest.approx(46.07, abs=.01)
            assert "Da" in outcome["message"] and "logP" in outcome["message"]
        else:
            assert outcome["facts"][0]["lipinski_violations"] == 0
            assert "Lipinski" in outcome["message"]


@pytest.mark.parametrize("name", ["property_calculator", "drug_likeness_assessment"])
def test_equivalent_and_repeated_user_rows_keep_their_literal_order(tools, name):
    values = ["CCO", "OCC", "CCO", "CC(O)C"]
    step = tools.bind(name, {"smiles": values}, user_texts=["Compare (CCO), OCC；CCO、CC(O)C."])
    result = tools.execute(step)
    assert result["ok"], result
    assert [row["smiles"] for row in result["facts"]] == values


def test_partial_or_changed_row_never_becomes_a_reusable_receipt(tools, monkeypatch):
    adapter = tools.registry.resolve("property_calculator")
    original = adapter.execute
    def incomplete(*args, **kwargs):
        result = original(*args, **kwargs)
        result.data.pop()
        return result
    monkeypatch.setattr(adapter, "execute", incomplete)
    step = tools.bind("property_calculator", {"smiles": ["CCO","CCN"]}, user_texts=["CCO、CCN"])
    assert tools.execute(step)["ok"] is False


def test_conflicting_provenance_cannot_be_rebound_as_a_verified_result(tools, monkeypatch):
    from src.agent.contracts import ToolProvenance
    adapter = tools.registry.resolve("property_calculator")
    original = adapter.execute
    def wrong_digest(*args, **kwargs):
        result = original(*args, **kwargs)
        result.provenance = ToolProvenance(adapter.spec.name, adapter.spec.version, output_digest="0" * 64)
        return result
    monkeypatch.setattr(adapter, "execute", wrong_digest)
    step = tools.bind("property_calculator", {"smiles": ["CCO"]}, user_texts=["CCO"])
    assert not tools.execute(step)["ok"]


def test_revision_after_submit_before_worker_start_suppresses_old_producer(tools, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from src.agent.tooling import adapters

    queued, release, invoked = Event(), Event(), Event()
    class QueuedExecutor(ThreadPoolExecutor):
        def submit(self, fn, *args):
            def start():
                queued.set()
                assert release.wait(5)
                return fn(*args)
            return super().submit(start)
    monkeypatch.setattr(adapters, "ThreadPoolExecutor", QueuedExecutor)
    adapter = tools.registry.resolve("property_calculator")
    monkeypatch.setattr(adapter.tool, "execute", lambda query: invoked.set())
    step = tools.bind("property_calculator", {"smiles": ["CCO"]}, user_texts=["CCO"])
    with ThreadPoolExecutor(1) as outer:
        work = outer.submit(tools.execute, step, dispatch_guard=lambda: not release.is_set())
        try:
            assert queued.wait(2)
        finally:
            release.set()
        assert work.result(timeout=3) is None
    assert not invoked.is_set()


def test_lookup_uses_verified_accession_and_exposes_only_structure_facts():
    from src.web.goal_tools import GoalTools
    class Lookup:
        name = "target_database_search"
        accession = "P06276"
        def execute(self, payload):
            assert payload == {"query": {"uniprot_id": "P06276"}, "resolution_limit": 20}
            return dict(tool_name=self.name, success=True, status="resolved", data=[dict(
                uniprot_id=self.accession, gene_symbol="BCHE", source="UniProt", structure_count=2,
                recommended_structures=[dict(structure_id="6SAM", source="RCSB_PDB", structure_type="experimental",
                    resolution=1.9, local_file_path="private.txt")])])
    lookup = Lookup()
    registry = build_tool_registry([lookup])
    try:
        goal = GoalTools(registry)
        args = {"uniprot_id": "P06276"}
        for source in ["XP06276", "https://example.org/P06276", "P06276-2"]:
            with pytest.raises(ValueError):
                goal.bind(lookup.name, args, user_texts=[source])
        for name, unsupported in [("admet_predictor", {"smiles": ["CCO"]}),
                                  (lookup.name, {"pdb_id": "6SAM"}), (lookup.name, {"uniprot_id": "6SAM"})]:
            with pytest.raises(ValueError):
                goal.bind(name, unsupported, user_texts=["验算 CCO 的 ADMET，读取 PDB 6SAM"])
        step = goal.bind(lookup.name, args, user_texts=["查 UniProt:P06276 的结构"])
        result = goal.execute(step)
        assert result["ok"], result
        assert result["facts"][0]["experimental_structure_preview"] == [{"pdb": "6SAM", "resolution_A": 1.9}]
        assert "private.txt" not in str(result)
        lookup.accession = "P15056"
        assert not goal.execute(step)["ok"]
    finally:
        registry.close()


def test_accession_with_many_structures_reuses_verified_experimental_metrics(monkeypatch):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    from src.web.goal_tools import GoalTools

    class Service:
        def search_targets(self, query, **kwargs):
            assert query == "P06276"
            return {"status": "resolved", "results": [{"target_id": 1, "uniprot_id": query,
                    "gene_symbol": "BCHE", "source": "UniProt", "structure_count": 25}]}

        def get_target_structures(self, target_id):
            return {"structures": [dict(structure_id=f"1A{i:02}", source="RCSB_PDB",
                    structure_type="experimental", resolution=3 - i / 100) for i in range(25)]}

    lookup = TargetDatabaseTool()
    monkeypatch.setattr(lookup, "_get_service", lambda: Service())
    registry = build_tool_registry([lookup])
    try:
        goal = GoalTools(registry)
        step = goal.bind(lookup.name, {"uniprot_id": "P06276"}, user_texts=["Retrieve P06276."])
        result = goal.execute(step)
        assert result["ok"], result
        fact, = result["facts"]
        assert fact["structure_count"] == fact["comparable_structure_count"] == 25
        assert len(fact["experimental_structure_preview"]) == 20
        assert fact["experimental_structure_preview"][0] == {"pdb": "1A24", "resolution_A": 2.76}
    finally:
        registry.close()
