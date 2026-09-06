from .property_calculator import PropertyCalculator
from .admet_predictor import ADMETPredictor
from .drug_likeness_assessment import DrugLikenessAssessment
from .llm_molecular_generator import LLMMolecularGenerator
from .candidate_ranker import CandidateRanker

# 核心工具列表 - 按重要性和使用频率排序
CORE_TOOLS = [
    'PropertyCalculator',
    'ADMETPredictor',
    'DrugLikenessAssessment',
    'LLMMolecularGenerator',
    'CandidateRanker',
]

# 可选工具列表 - 延迟加载以提升性能
OPTIONAL_TOOLS = [
    'MolecularDocking',
    'ReverseTargetTool',
    'TargetDatabaseTool',
    'ActivityPredictorTool',
    'RAGSearchTool',
    'RXNChemistryAgent',
]

__all__ = CORE_TOOLS + OPTIONAL_TOOLS

def get_core_tools(molecular_generator_llm=None):
    """获取核心工具实例"""
    import logging
    logger = logging.getLogger(__name__)
    
    generator_llm = molecular_generator_llm
    if generator_llm is None:
        try:
            from src.web.models.ollama_model import OllamaModel
            generator_llm = OllamaModel(
                base_url="http://localhost:11434",
                model_name="gmm-llama:latest"
            )
            logger.info("Created fallback gmm-llama:latest connection for molecular generation")
        except Exception as e:
            logger.warning(f"Unable to create fallback gmm-llama connection: {e}")
    
    return [
        PropertyCalculator(),
        ADMETPredictor(),
        DrugLikenessAssessment(),
        LLMMolecularGenerator(llm_model=generator_llm),
        CandidateRanker(),
    ]

def get_optional_tool(tool_name, llm=None):
    """延迟加载可选工具"""
    if tool_name == 'MolecularDocking':
        from .molecular_docking import MolecularDocking
        return MolecularDocking()
    elif tool_name == 'ReverseTargetTool':
        from .reverse_target_tool import ReverseTargetTool
        return ReverseTargetTool()
    elif tool_name == 'TargetDatabaseTool':
        from .target_database_tool import TargetDatabaseTool
        return TargetDatabaseTool()
    elif tool_name == 'ActivityPredictorTool':
        from .activity_predictor_tool import ActivityPredictorTool
        return ActivityPredictorTool()
    elif tool_name == 'RAGSearchTool':
        from .rag_search_tool import RAGSearchTool
        return RAGSearchTool()
    elif tool_name == 'RXNChemistryAgent':
        from .rxn_chemistry_agent import RXNChemistryAgent
        return RXNChemistryAgent()
    else:
        raise ValueError(f"Unknown optional tool: {tool_name}")


def get_all_tools(molecular_generator_llm=None):
    """获取所有工具实例（核心 + 可选），用于 Skill 系统的工具池。"""
    import logging
    logger = logging.getLogger(__name__)

    tools = get_core_tools(molecular_generator_llm)

    for tool_name in OPTIONAL_TOOLS:
        try:
            tool = get_optional_tool(tool_name)
            tools.append(tool)
            logger.info(f"✅ 可选工具加载成功: {tool_name}")
        except Exception as e:
            logger.warning(f"⚠️ 可选工具加载失败（跳过）: {tool_name} - {e}")

    return tools
