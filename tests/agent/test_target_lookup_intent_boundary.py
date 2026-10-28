"""A target structure lookup is not molecular generation or docking."""
import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.generation_request import has_generation_intent, preflight_generation_request
from src.agent.contracts.generation_request import GenerationRequestError
from src.agent.contracts.web_requirements import WEB_TOOLS
from src.agent.contracts.docking_intent import explicit_docking_action
from src.agent.router import SkillRouter
from src.agent.scientific_session.web_requirements import compile_web_requirements


LOOKUPS = [
    '帮我查找人源 EGFR 的靶点结构，给我适合小分子对接的结构。',
    '查找 EGFR 蛋白结构，提供适合小分子对接的结构。',
    '查找 EGFR 的结构，给我适合小分子对接的结构。',
    '查找 EGFR 结构，给我适合小分子对接的结构。',
    '查找 EGFR 的三维结构，给我适合小分子对接的结构。',
    '查找 EGFR 靶点结构，提供适合小分子对接的三维结构。',
    '查找 EGFR 靶点结构，提供适合小分子对接的高质量结构。',
    '查找 EGFR 靶点结构，提供适合小分子对接的蛋白三维结构。',
    '查找 EGFR 靶点结构，提供适合小分子对接的结构文件。',
    '查找 EGFR 靶点结构，给我最适合小分子对接的结构。',
    '查找 EGFR 靶点结构，给我适合用于小分子对接的结构。',
    '查找 EGFR 靶点结构，给我适合小分子对接的结构（实验结构）。',
    '查找 EGFR 靶点结构，给我适合小分子对接的结构及其分辨率。',
    '查找 EGFR 靶点结构，能否把结构用于小分子对接？',
    '查找 EGFR 靶点结构，是否可以把 CCO 用于对接？',
    '查找 EGFR 靶点结构，适合用作小分子对接的结构。',
    '查找 EGFR 靶点结构，哪些结构可用来进行分子对接？',
    '查找 EGFR 蛋白结构，只查结构不进行对接。',
    '查找 EGFR 靶点结构，它能用于对接吗？',
    'Find EGFR protein structures suitable for small-molecule docking.',
]


@pytest.mark.parametrize('query', LOOKUPS)
def test_structure_lookup_does_not_authorize_generation_or_docking(query):
    assert not has_generation_intent(query)
    assert preflight_generation_request(query) is None
    decision = SkillRouter().decide(query, llm=None)
    assert decision.selected_skill == 'target_database_search'
    assert 'llm_molecular_generator' not in decision.allowed_tools
    assert 'molecular_docking' not in decision.allowed_tools
    req = compile_web_requirements(AgentContext(query, 'lookup-trace', session_id='lookup'),
                                   allowed_tools=WEB_TOOLS)
    assert [step.tool_name for step in req.steps] == ['target_database_search']


@pytest.mark.parametrize('query', [
    '查找 EGFR 蛋白结构，并生成 3 个小分子候选。',
    '查找 EGFR 靶点结构，给我 3 个适合 EGFR 的小分子。',
    '查找 EGFR 的靶点结构，给我适合小分子对接的结构，并生成 3 个候选分子。',
])
def test_independent_explicit_generation_is_not_suppressed(query):
    assert has_generation_intent(query)
    assert preflight_generation_request(query) == 3
    try:
        req = compile_web_requirements(AgentContext(query, 'explicit-gen', session_id='lookup'),
                                       allowed_tools=WEB_TOOLS)
    except ValueError:
        return  # Explicit rejection is safer than silently losing an obligation.
    assert 'llm_molecular_generator' in {step.tool_name for step in req.steps}


def test_ligand_structure_is_not_mistaken_for_protein_lookup():
    query = '查找 EGFR 靶点结构，给我适合小分子对接的候选分子结构。'
    assert has_generation_intent(query)


def test_explicit_creation_is_not_masked_by_structure_suitability():
    assert has_generation_intent('查找 EGFR 靶点结构，给我生成适合小分子对接的结构。')


def test_independent_actual_docking_is_not_a_lookup_purpose():
    query = '查找 EGFR 靶点结构，然后对 CCO 实际运行分子对接。'
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, 'explicit-dock', session_id='lookup'),
                                 allowed_tools=WEB_TOOLS)


def test_structures_for_docking_do_not_erase_a_separate_docking_action():
    query = '查找 EGFR 的靶点结构，给我适合小分子对接的结构，然后对 CCO 运行分子对接。'
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, 'explicit-dock', session_id='lookup'),
                                 allowed_tools=WEB_TOOLS)


@pytest.mark.parametrize('query', [
    '生成 2 个分子，再生成 3,abc 个分子',
    '生成 2 个分子，再生成 3, abc 个分子',
    '生成 2 个分子，再生成 3,+1 个分子',
    '生成 2 个分子，再生成 3, -1 个分子',
    '生成 2 个分子，再生成 3,.5 个分子',
    '生成 2 个分子，再生成 3,∞ 个分子',
    '生成 2 个分子，再生成 3,,1 个分子',
    '生成 2 个分子，再生成 3,负一 个分子',
])
def test_malformed_later_count_does_not_become_first_valid_count(query):
    with pytest.raises(GenerationRequestError):
        preflight_generation_request(query)


@pytest.mark.parametrize('query', [
    '查找 EGFR 靶点结构，然后把 CCO 用于小分子对接。',
    '把 CCO 用于小分子对接。',
    '查找 EGFR 靶点结构，随后用 CCO 进行小分子对接。',
    '查找 EGFR 靶点结构，使用 CCO 对接。',
    '查找 EGFR 靶点结构，随后对接 CCO。',
    '查找 EGFR 靶点结构，然后让 CCO 和 EGFR 对接。',
    '查找 EGFR 靶点结构，然后对接CCO。',
    '查找 EGFR 靶点结构，将 CCO 与 EGFR 对接。',
    'Find EGFR protein structures and dock CCO to EGFR.',
    '查找 EGFR 适合对接的结构并对接CCO。',
    '查找 EGFR 适合小分子对接的结构，并对 CCO 执行分子对接。',
    '查找 EGFR 靶点结构，将该蛋白结构用于对接 CCO。',
    '查找 EGFR 适合对接的结构后用 CCO 对接。',
])
def test_separate_ligand_docking_is_never_silently_dropped(query):
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, 'explicit-dock', session_id='lookup'),
                                 allowed_tools=WEB_TOOLS)


def test_explicitly_forbidden_docking_does_not_block_structure_lookup():
    query = '查找 EGFR 靶点结构，不要把 CCO 用于小分子对接。'
    req = compile_web_requirements(AgentContext(query, 'no-dock', session_id='lookup'),
                                   allowed_tools=WEB_TOOLS)
    assert [step.tool_name for step in req.steps] == ['target_database_search']


def test_explicit_structure_then_ligand_execution_request_is_docking_action():
    query = (
        '使用 EGFR 的实验结构 1M17 与候选分子 '
        'CC(=O)N1CCN(C(=O)C2(c3ccc(F)cc3)CC2)CC1 进行一次分子对接；'
        '先确认结构和配体角色，若口袋不能由结构证据确认就先说明，不要猜坐标。'
    )
    assert explicit_docking_action(query)
