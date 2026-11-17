"""Offline WebSocket wiring checks, not real model/browser acceptance."""
import json
import pytest

from tests.agent.pi_web_support import cleanup_web_handlers, make_web_handler, run_web_turn
from tests.agent.test_generation_ranking_contract import CountingTool, GEN, output
from tests.agent.test_generation_transport_characterization import offline_models
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
from tests.test_scientific_reference_target_context import confirmed, pointer, FOLLOWUP, SOURCE


@pytest.mark.parametrize("query", [FOLLOWUP, FOLLOWUP.replace("优化", "结构改造")])
def test_handler_passes_confirmed_source_target_to_optimizer(tmp_path, confirmed, query):
    store, service, view, _ = confirmed
    generator = CountingTool(GEN, output(GEN))
    unavailable = [CountingTool(name, ToolResult.error_result(name,
        AgentErrorCode.MODEL_UNAVAILABLE, 'offline synthetic unavailable'))
        for name in ('admet_predictor', 'activity_predictor')]
    handler = make_web_handler([generator, PropertyCalculator(), *unavailable,
                                LeadOptimizationVerifier()], store)
    handler.scientific_references = service
    calls = []
    required = []
    original = handler.process_decision_message
    async def capture(*args, **kwargs):
        calls.append(kwargs['context'])
        required.append((kwargs['requirements'].workflow_name, kwargs['required_tools']))
        return await original(*args, **kwargs)
    handler.process_decision_message = capture
    run_web_turn(handler, query, [], 'owner', reference=pointer(view))
    assert len(calls) == 1
    assert required[0][1] <= set(handler.agent_system.tools), required
    assert calls[0].query.endswith("\n靶点: PDE")
    assert calls[0].resolved_molecule.canonical_smiles == "CCN"
    assert generator.calls == 1, json.dumps(handler.web_results[-1].to_legacy_dict(), ensure_ascii=False)
    assert generator.inputs[0]['outputs']['baseline']


@pytest.mark.parametrize('query', [FOLLOWUP + '\n靶点: PDE\nSMILES: CCN',
    '请改造这个分子 SMILES: CCO，给出两个候选。',
    '将 SMILES: CCO 改造成两个针对 PDE 的候选分子',
    '对分子 SMILES: CCO 做两种结构改造，比较改造前后对 PDE 的活性和分子量。',
    '优化 CCN，针对 BuChE 降低 logP', 'Lead optimization for CCN targeting PDE5A'])
def test_target_hint_does_not_replace_explicit_lead_optimization(query):
    from src.agent.routing.hybrid import HybridSkillRouter
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == 'hit_to_lead_optimization'
    assert 'target_database_search' not in decision.allowed_tools


@pytest.mark.parametrize("query", ["请解释分子改造的含义。", "不要改造分子，只计算 CCO 的分子量。"])
def test_discussing_or_declining_modification_does_not_run_optimizer(query):
    from src.agent.routing.hybrid import HybridSkillRouter
    assert HybridSkillRouter().decide(query).selected_skill != "hit_to_lead_optimization"


@pytest.mark.parametrize("query", [
    "请改造分子 SMILES: CCO，优先降低 logP，给出两个候选。不要做 ADMET。",
    "SMILES: CCO，优先降低 logP，返回两个新 SMILES。不要做 ADMET。",
])
def test_modification_turn_delivers_verified_parent_candidate_comparison(tmp_path, offline_models, query):
    model = offline_models("OCC\nOCCO\nCC(O)CO")
    handler = make_web_handler([LLMMolecularGenerator(model), PropertyCalculator(),
                                LeadOptimizationVerifier()], SQLiteAgentStateStore(tmp_path / "state.sqlite"))
    complete, _ = run_web_turn(handler, query, [], "owner")
    assert complete["status"] == "completed", complete["content"]
    assert len(model.prompts) == 1 and "Original SMILES: CCO\n" in model.prompts[0]
    assert "基线结构：`CCO`" in complete["content"]
    assert "| OCCO | logP | -0.001 | -1.029 | 满足比较目标 |" in complete["content"]
    assert "| CC(O)CO | logP | -0.001 | -0.641 | 满足比较目标 |" in complete["content"]


def test_generator_receives_the_displayed_second_parent(confirmed, offline_models):
    store, service, view, _ = confirmed
    model = offline_models("NCC\nNCCO\nNCC(O)CO")
    handler = make_web_handler([LLMMolecularGenerator(model), PropertyCalculator(),
                                LeadOptimizationVerifier()], store)
    handler.scientific_references = service
    query = "对第2个候选做两种结构改造，降低 logP。不要做 ADMET。"
    complete, _ = run_web_turn(handler, query, [], "owner", reference=pointer(view))
    assert complete["status"] == "completed", complete["content"]
    assert len(model.prompts) == 1
    assert "Original SMILES: CCN\n" in model.prompts[0]
    assert "基线结构：`CCN`" in complete["content"]


@pytest.mark.parametrize("parent,candidate", [
    pytest.param("Cn1c(=O)c2c(ncn2C)n(C)c1=O",
                 "OCn1c(=O)c2c(ncn2C)n(C)c1=O", id="caffeine"),
    pytest.param("CC(C)(CO)[C@@H](O)C(=O)NCCC(=O)[O-].[Na+]",
                 "OCC(C)(CO)[C@@H](O)C(=O)NCCC(=O)[O-].[Na+]", id="sodium-salt"),
    pytest.param("Nc1ncnc2c1ncn2[C@@H]1O[C@H](CO)[C@@H](O)[C@H]1O",
                 "Nc1ncnc2c1ncn2[C@@H]1O[C@H](CCO)[C@@H](O)[C@H]1O",
                 id="adenosine-goal-missed"),
    pytest.param("Cc1cc2nc3c(=O)[nH]c(=O)nc-3n(C[C@H](O)[C@H](O)[C@H](O)CO)c2cc1C",
                 "OCc1cc2nc3c(=O)[nH]c(=O)nc-3n(C[C@H](O)[C@H](O)[C@H](O)CO)c2cc1C", id="flavin"),
    pytest.param("O=C1N[C@@H](Cc2ccccc2)C(=O)N[C@@H]1CO",
                 "O=C1N[C@@H](Cc2ccc(O)cc2)C(=O)N[C@@H]1CO", id="cyclic-dipeptide"),
    pytest.param(r"CCCCCCCC/C=C\CCCCCCCC(=O)OC[C@H](O)COP(=O)([O-])OCC[N+](C)(C)C",
                 r"OCCCCCCCC/C=C\CCCCCCCC(=O)OC[C@H](O)COP(=O)([O-])OCC[N+](C)(C)C", id="phospholipid"),
    pytest.param("C[C@H](CCCC(C)C)[C@H]1CC[C@@H]2[C@@H]3CC=C4C[C@@H](O)CC[C@]4(C)[C@H]3CC[C@]12C",
                 "C[C@H](CCCC(C)CO)[C@H]1CC[C@@H]2[C@@H]3CC=C4C[C@@H](O)CC[C@]4(C)[C@H]3CC[C@]12C", id="sterol"),
    pytest.param("N[C@@H](Cc1ccc(O)cc1)C(=O)N[13CH2]C(=O)O",
                 "N[C@@H](Cc1cc(O)c(O)cc1)C(=O)N[13CH2]C(=O)O", id="isotope-peptide"),
])
def test_complex_modification_preserves_comparison(tmp_path, offline_models, parent, candidate):
    from rdkit import Chem
    from rdkit.Chem import Crippen

    original, product = map(Chem.MolFromSmiles, (parent, candidate))
    echo = Chem.MolToSmiles(original, rootedAtAtom=1)
    model = offline_models(f"{echo}\n{candidate}")
    handler = make_web_handler([LLMMolecularGenerator(model), PropertyCalculator(),
                                LeadOptimizationVerifier()], SQLiteAgentStateStore(tmp_path / "state.sqlite"))
    query = f"请改造这个分子，降低 logP，返回一个新 SMILES。不要做 ADMET。\nSMILES: {parent}"
    complete, _ = run_web_turn(handler, query, [], "owner", result_format="summary_details_v1")
    assert len(model.prompts) == 1
    seed = model.prompts[0].split("Original SMILES: ", 1)[1].splitlines()[0]
    assert Chem.MolToSmiles(Chem.MolFromSmiles(seed)) == Chem.MolToSmiles(original)
    baseline, value = (round(Crippen.MolLogP(mol), 3) for mol in (original, product))
    verdict = "满足比较目标" if value < baseline else "未满足比较目标"
    assert complete["status"] == ("completed" if value < baseline else "partial")
    rows = [row for row in complete["content"].splitlines() if " | logP | " in row
            and row.endswith(("| 满足比较目标 |", "| 未满足比较目标 |"))]
    assert rows == [f"| {Chem.MolToSmiles(product)} | logP | {baseline} | {value} | {verdict} |"]


def test_handler_displays_independent_generation_constraint_summary(tmp_path):
    generator = CountingTool(GEN, output(GEN))
    handler = make_web_handler([generator, PropertyCalculator()],
                               SQLiteAgentStateStore(tmp_path / 'state.sqlite'))
    complete, frames = run_web_turn(handler,
        '生成1个分子，计算理化性质，要求分子量小于450、logP小于-1', [], 'owner', mol_count=1)
    answer = complete['content']
    assert "用户约束逐项检查" in answer
    assert "46.07" in answer and "-0.001 | 不满足" in answer
    assert complete['status'] == 'partial'
    public = next(frame for frame in frames if frame['type'] == 'agent_result')
    audit = json.loads(public['final_answer'])
    assert len(audit['observations']) == 2
    assert public['final_answer'] == handler.web_results[-1].final_answer


@pytest.mark.parametrize("target", ["PDE", "BuChE"])
def test_resolved_candidate_optimization_query_reaches_activity_parser(target):
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    payload = {"query": FOLLOWUP + "\n靶点: " + target, "smiles": ["CCN"], "target": target}
    _, smiles, resolved_target = parse_activity_input(payload, ActivityPredictorTool())
    assert smiles == ["CCN"] and resolved_target == target
    payload["query"] += "\n针对 UNKNOWN"
    with pytest.raises(ValueError):
        parse_activity_input(payload, ActivityPredictorTool())


def test_candidate_ordinal_prefix_cannot_hide_unknown_protein_label():
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    with pytest.raises(ValueError):
        parse_activity_input({"query": "对第2个候选蛋白 UNKNOWN 的活性；靶点: PDE", "smiles": ["CCN"]}, ActivityPredictorTool())


@pytest.mark.parametrize("target", ["UNKNOWN", "EGFR"])
def test_ordinal_possessive_cannot_hide_explicit_target(target):
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    with pytest.raises(ValueError):
        parse_activity_input({"query": "对第2个候选的 " + target + " 活性进行预测；靶点: PDE",
                              "smiles": ["CCN"]}, ActivityPredictorTool())
