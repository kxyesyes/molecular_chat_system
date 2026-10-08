from src.agent import prompts


def test_prompt_module_keeps_only_active_system_prompt():
    assert hasattr(prompts, "DRUG_DESIGN_SYSTEM_PROMPT")
    for removed_name in (
        "PREFIX",
        "SUFFIX",
        "REACT_FORMAT",
        "SIMPLE_TOOL_TEMPLATE",
        "AGENT_TOOL_SELECTION_PROMPT",
    ):
        assert not hasattr(prompts, removed_name)
