from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections import defaultdict
from typing import Any

from src.agent.persistence.base import AgentStateStore
from src.agent.skills.skill_registry import SkillRegistry
from src.agent.tooling import ToolRegistry

from .models import RouteCandidate, RouteDecision


SKILL_TOOLS = {
    "admet_assessment": [
        "property_calculator",
        "admet_predictor",
        "drug_likeness_assessment",
    ],
    "activity_prediction": ["activity_predictor"],
    "reverse_target_prediction": ["reverse_target_predictor"],
    "target_database_search": ["target_database_search"],
    "molecular_design": ["llm_molecular_generator"],
    "docking_simulation": ["molecular_docking"],
    "comprehensive_evaluation": [
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ],
    "hit_to_lead_optimization": [
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "llm_molecular_generator",
        "property_calculator",
    ],
    "target_driven_design": [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "molecular_docking",
    ],
    "rag_search": ["rag_search"],
}

TARGET_PATTERN = re.compile(
    r"\b(PDE\d+[A-Z]?|EGFR|BACE1|KRAS|BRAF|JAK2|ALK|MET|CDK2|KDR)\b", re.I
)
SMILES_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[BCNOPSFIK]|Cl|Br)[A-Za-z0-9@+\-\[\]\(\)=#./\\]{2,}(?![A-Za-z])"
)


class HybridSkillRouter:
    def __init__(
        self,
        registry: SkillRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        state_store: AgentStateStore | None = None,
        llm: Any = None,
        llm_margin_threshold: float = 0.12,
        minimum_confidence: float = 0.3,
    ):
        self.registry = registry or SkillRegistry()
        self.tool_registry = tool_registry
        self.state_store = state_store
        self.llm = llm
        self.llm_margin_threshold = llm_margin_threshold
        self.minimum_confidence = minimum_confidence

    def decide(
        self,
        query: str,
        memory: list[dict[str, Any]] | None = None,
    ) -> RouteDecision:
        text = query.strip()
        lower = text.lower()
        if self._is_general_chat(lower):
            return RouteDecision(
                selected_skill=None,
                confidence=1.0,
                source="fallback",
                reasons=["No supported scientific task intent was detected"],
            )

        scores: dict[str, float] = defaultdict(float)
        reasons: dict[str, list[str]] = defaultdict(list)

        def add(skill: str, value: float, reason: str) -> None:
            scores[skill] += value
            reasons[skill].append(reason)

        has_target = bool(TARGET_PATTERN.search(text))
        has_smiles = bool(SMILES_PATTERN.search(text)) or "CCO" in text.upper()
        generation = self._contains(lower, "生成", "设计", "generate", "design")

        if self._contains(lower, "全面", "综合", "成药性", "comprehensive", "full analysis"):
            add("comprehensive_evaluation", 0.95, "comprehensive workflow phrase")
        if self._contains(lower, "优化", "降低", "提高", "改善", "hit-to-lead", "lead optimization"):
            add("hit_to_lead_optimization", 0.95, "lead optimization intent")
        knowledge_intent = self._contains(
            lower,
            "知识库",
            "本地知识库",
            "引用",
            "来源",
            "检索 medchat",
            "hinge binder",
            "常识解释",
            "模型推断",
            "autodock vina docking box",
            "rag",
            "knowledge base",
            "citation",
            "source",
        )

        if has_target and generation:
            add("target_driven_design", 1.0, "target plus design workflow intent")
        if generation:
            add("molecular_design", 0.78, "molecular generation intent")
        if knowledge_intent:
            add("rag_search", 1.0, "local knowledge or sourced explanation intent")
        if self._contains(lower, "完整工作流", "完整流程", "全流程", "full workflow"):
            add("comprehensive_evaluation", 1.0, "explicit full workflow intent")
        if self._contains(lower, "pdb", "蛋白结构", "可用于 docking 的", "查 ", "搜索"):
            add("target_database_search", 0.92, "target structure search intent")
        if self._contains(lower, "哪些靶点", "作用于哪些", "潜在靶点", "可能作用", "reverse target"):
            add("reverse_target_prediction", 0.95, "reverse target intent")
        if self._contains(lower, "pic50", "活性", "activity"):
            add("activity_prediction", 0.9, "activity endpoint requested")
        if self._contains(
            lower,
            "admet",
            "分子量",
            "logp",
            "qed",
            "tpsa",
            "lipinski",
            "基础性质",
            "属性",
        ):
            add("admet_assessment", 0.88, "property or ADMET endpoint requested")
        if self._contains(lower, "docking", "对接", "结合能", "binding affinity"):
            add("docking_simulation", 0.82, "docking intent")
        if self._contains(lower, ".pdb", ".pdbqt") and self._contains(
            lower,
            ".sdf",
            ".mol2",
            "ligand_path",
            "配体",
        ) and self._contains(lower, "center", "size", "box"):
            add("docking_simulation", 1.0, "structured docking files and box intent")
        if has_target and self._contains(
            lower,
            "比较",
            "差异",
            "准备",
            "结构信息",
            "靶点数据库",
            "compare",
            "structure information",
        ):
            add("target_database_search", 0.95, "target structure comparison intent")
        if has_target:
            add("target_database_search", 0.12, "recognized target entity")
        if has_smiles:
            for skill in (
                "admet_assessment",
                "activity_prediction",
                "reverse_target_prediction",
                "comprehensive_evaluation",
            ):
                if skill in scores:
                    add(skill, 0.05, "recognized molecular structure input")

        if generation and self._contains(lower, "并告诉", "同时告诉", "and tell"):
            scores["molecular_design"] = max(scores["molecular_design"], 0.98)
            reasons["molecular_design"].append(
                "generation request remains bounded to the generation skill"
            )
        if (
            "admet" in lower
            and self._contains(lower, "并告诉", "同时告诉", "and tell")
            and self._contains(lower, "靶点", "target")
        ):
            scores["admet_assessment"] = 1.0
            scores["reverse_target_prediction"] = min(
                scores["reverse_target_prediction"], 0.85
            )
            reasons["admet_assessment"].append(
                "ADMET request remains primary; target prediction is a follow-up task"
            )
        if (
            self._contains(lower, "admet", "类药性")
            and not generation
            and not self._contains(
                lower,
                "全面",
                "综合",
                "成药性",
                "comprehensive",
                "full analysis",
            )
            and not self._contains(
                lower,
                "优化这个",
                "优化分子",
                "结构优化",
                "先导",
                "hit-to-lead",
                "lead optimization",
            )
        ):
            scores["admet_assessment"] = 1.0
            scores["hit_to_lead_optimization"] = min(
                scores["hit_to_lead_optimization"],
                0.75,
            )
            reasons["admet_assessment"].append(
                "Explicit ADMET and drug-likeness evaluation remains primary"
            )

        self._apply_feedback(text, scores, reasons)
        self._apply_memory(memory or [], scores, reasons)
        self._apply_tool_availability(scores, reasons)

        candidates = self._rank(scores, reasons)
        if not candidates or candidates[0].score < self.minimum_confidence:
            return RouteDecision(
                selected_skill=None,
                confidence=candidates[0].score if candidates else 0.0,
                candidates=candidates,
                reasons=["Routing confidence is below the execution threshold"],
                source="fallback",
                requires_confirmation=True,
            )

        top = candidates[0]
        margin = top.score - (candidates[1].score if len(candidates) > 1 else 0.0)
        selected = top.skill_name
        source = "rule" if top.score >= 0.9 and margin >= 0.2 else "scoring"
        decision_reasons = list(top.reasons)

        if self.llm and len(candidates) > 1 and margin < self.llm_margin_threshold:
            arbitration = self._llm_arbitrate(text, candidates[:3])
            allowed = {candidate.skill_name for candidate in candidates[:3]}
            if arbitration and arbitration.get("selected_skill") in allowed:
                selected = arbitration["selected_skill"]
                source = "llm"
                decision_reasons = arbitration.get("reasons", [])
                top = next(item for item in candidates if item.skill_name == selected)
                top.score = max(
                    top.score,
                    min(1.0, float(arbitration.get("confidence", top.score))),
                )

        confirmation_reasons = self._confirmation_reasons(
            selected, lower, has_smiles
        )
        return RouteDecision(
            selected_skill=selected,
            confidence=top.score,
            candidates=candidates,
            reasons=decision_reasons + confirmation_reasons,
            source=source,
            requires_confirmation=bool(confirmation_reasons),
            allowed_tools=SKILL_TOOLS.get(selected, []),
        )

    @staticmethod
    def _contains(text: str, *needles: str) -> bool:
        return any(needle in text for needle in needles)

    @staticmethod
    def _is_general_chat(text: str) -> bool:
        greetings = ("你好", "您好", "心情", "早上好", "晚上好", "hello", "hi ")
        scientific = (
            "smiles",
            "分子",
            "靶点",
            "admet",
            "logp",
            "qed",
            "docking",
            "pdb",
            "活性",
        )
        return any(item in text for item in greetings) and not any(
            item in text for item in scientific
        )

    def _apply_feedback(
        self,
        query: str,
        scores: dict[str, float],
        reasons: dict[str, list[str]],
    ) -> None:
        if not self.state_store:
            return
        for item in self.state_store.get_routing_feedback(query):
            if item.get("accepted") and item.get("corrected_skill"):
                skill = item["corrected_skill"]
                scores[skill] = max(scores[skill], 1.0)
                reasons[skill].append("accepted routing feedback")

    @staticmethod
    def _apply_memory(
        memory: list[dict[str, Any]],
        scores: dict[str, float],
        reasons: dict[str, list[str]],
    ) -> None:
        for item in memory:
            preferred = item.get("content", {}).get("preferred_skill")
            if preferred:
                scores[preferred] += 0.05
                reasons[preferred].append("confirmed memory preference")

    def _apply_tool_availability(
        self,
        scores: dict[str, float],
        reasons: dict[str, list[str]],
    ) -> None:
        if not self.tool_registry:
            return
        for skill, score in list(scores.items()):
            unavailable = []
            for tool_name in SKILL_TOOLS.get(skill, []):
                try:
                    self.tool_registry.resolve(tool_name)
                except (KeyError, RuntimeError):
                    unavailable.append(tool_name)
            if unavailable:
                scores[skill] = max(0.0, score - min(0.4, 0.1 * len(unavailable)))
                reasons[skill].append(
                    f"unavailable tools: {', '.join(sorted(unavailable))}"
                )

    @staticmethod
    def _rank(
        scores: dict[str, float],
        reasons: dict[str, list[str]],
    ) -> list[RouteCandidate]:
        return [
            RouteCandidate(
                skill_name=skill,
                score=min(1.0, max(0.0, score)),
                reasons=reasons[skill],
            )
            for skill, score in sorted(
                scores.items(), key=lambda item: (-item[1], item[0])
            )
            if score > 0
        ]

    @staticmethod
    def _confirmation_reasons(
        skill: str, lower: str, has_smiles: bool
    ) -> list[str]:
        reasons = []
        if skill in {
            "admet_assessment",
            "activity_prediction",
            "reverse_target_prediction",
            "comprehensive_evaluation",
            "hit_to_lead_optimization",
        } and not has_smiles:
            reasons.append("A valid SMILES input is required before execution")
        if skill == "docking_simulation":
            has_receptor = any(
                marker in lower
                for marker in (".pdb", ".pdbqt", "receptor file", "受体文件")
            )
            has_box = any(
                marker in lower
                for marker in ("box", "center_", "size_", "口袋坐标")
            )
            if not (has_receptor and has_box):
                reasons.append(
                    "Docking requires receptor input and docking-box parameters"
                )
        if skill == "molecular_design" and any(
            marker in lower
            for marker in ("pic50", "admet", "docking", "结合能")
        ):
            reasons.append(
                "Generation can produce SMILES only; scientific evaluations require follow-up tools"
            )
        if skill == "admet_assessment" and any(
            marker in lower for marker in ("靶点", "target")
        ):
            reasons.append(
                "Target prediction requires a separate reverse-target tool call"
            )
        return reasons

    def _llm_arbitrate(
        self, query: str, candidates: list[RouteCandidate]
    ) -> dict[str, Any] | None:
        prompt = json.dumps(
            {
                "task": "Select exactly one skill from candidate_skills",
                "query": query,
                "candidate_skills": [item.model_dump() for item in candidates],
                "output_schema": {
                    "selected_skill": "string",
                    "confidence": "number 0..1",
                    "reasons": ["string"],
                },
            },
            ensure_ascii=False,
        )
        try:
            response = self.llm.generate(prompt, temperature=0.0, max_tokens=200)
            if inspect.isawaitable(response):
                response = asyncio.run(response)
            data = json.loads(str(response).strip().strip("`"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None
