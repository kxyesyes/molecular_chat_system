"""Pure dispatch selection; no tool execution or plan construction."""
from collections.abc import Callable


def select_workflow(skill: str, query: str, *, looks_like_design: Callable[[str], bool]) -> str | None:
    if skill == "admet_assessment":
        return skill
    if skill in {"comprehensive_evaluation", "comprehensive_evaluation_skill"}:
        return "comprehensive_evaluation"
    if skill == "target_driven_design" or (
        skill == "target_database_search" and looks_like_design(query)
    ):
        return "target_driven_design"
    if skill in {
        "hit_to_lead_optimization", "molecular_design", "target_database_search",
        "docking_simulation", "activity_prediction", "reverse_target_prediction", "rag_search",
    }:
        return skill
    return None
