from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class WorkflowPolicy:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    is_multi_step: bool = False


WORKFLOW_POLICIES = (
    WorkflowPolicy(
        "comprehensive_evaluation",
        "对候选分子执行性质、类药性、ADMET、活性、反向寻靶和靶点结构综合评价。",
        (
            "property_calculator",
            "drug_likeness_assessment",
            "admet_predictor",
            "activity_predictor",
            "reverse_target_predictor",
            "target_database_search",
        ),
        True,
    ),
    WorkflowPolicy(
        "hit_to_lead_optimization",
        "先诊断原分子，再生成和比较优化候选。",
        (
            "property_calculator",
            "drug_likeness_assessment",
            "admet_predictor",
            "activity_predictor",
            "llm_molecular_generator",
        ),
        True,
    ),
    WorkflowPolicy(
        "target_driven_design",
        "检索靶点后生成并筛选候选分子。",
        (
            "target_database_search",
            "llm_molecular_generator",
            "property_calculator",
            "admet_predictor",
            "activity_predictor",
            "candidate_ranker",
            "molecular_docking",
        ),
        True,
    ),
    WorkflowPolicy(
        "molecular_design",
        "使用本地生成模型生成候选分子。",
        ("llm_molecular_generator",),
    ),
    WorkflowPolicy(
        "activity_prediction",
        "使用真实活性模型预测分子活性。",
        ("activity_predictor",),
    ),
    WorkflowPolicy(
        "reverse_target_prediction",
        "基于结构证据预测潜在靶点。",
        ("reverse_target_predictor",),
    ),
    WorkflowPolicy(
        "target_database_search",
        "检索靶点和结构数据库。",
        ("target_database_search",),
    ),
    WorkflowPolicy(
        "admet_assessment",
        "计算性质、类药性和 ADMET。",
        (
            "property_calculator",
            "admet_predictor",
            "drug_likeness_assessment",
        ),
        True,
    ),
    WorkflowPolicy(
        "docking_simulation",
        "使用结构化受体、配体和 box 输入执行 docking。",
        (
            "molecular_docking",
            "prepare_receptor",
            "prepare_ligand",
            "run_docking",
            "get_docking_result",
        ),
    ),
    WorkflowPolicy(
        "rag_search",
        "检索本地知识库并保留来源。",
        ("rag_search",),
    ),
)


class WorkflowCatalog:
    def __init__(
        self, policies: Iterable[WorkflowPolicy] = WORKFLOW_POLICIES
    ) -> None:
        self._policies = tuple(policies)
        self._by_name = {policy.name: policy for policy in self._policies}
        if len(self._by_name) != len(self._policies):
            raise ValueError("Workflow policy names must be unique")

    @property
    def policies(self) -> tuple[WorkflowPolicy, ...]:
        return self._policies

    def get(self, name: str) -> WorkflowPolicy | None:
        return self._by_name.get(name)

    def require(self, name: str) -> WorkflowPolicy:
        policy = self.get(name)
        if policy is None:
            raise KeyError(f"Unknown workflow: {name}")
        return policy

    def llm_catalog(self) -> str:
        return "\n".join(
            f"{index}. **{policy.name}**: {policy.description}"
            for index, policy in enumerate(self._policies, start=1)
        )
