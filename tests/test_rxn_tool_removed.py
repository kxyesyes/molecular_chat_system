from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS_INIT = (ROOT / "src" / "agent" / "tools" / "__init__.py").read_text(encoding="utf-8")
FACTORY = (ROOT / "src" / "agent" / "tooling" / "factory.py").read_text(encoding="utf-8")
PROMPTS = (ROOT / "src" / "agent" / "prompts.py").read_text(encoding="utf-8")
CHAT_HANDLER = (ROOT / "src" / "web" / "chat_handler.py").read_text(encoding="utf-8")


def test_legacy_reaction_tool_is_not_part_of_the_runtime_surface():
    tool_path = ROOT / "src" / "agent" / "tools" / "rxn_chemistry_agent.py"

    assert "RXNChemistryAgent" not in TOOLS_INIT
    assert "rxn_chemistry_agent" not in FACTORY
    assert "rxn_chemistry_agent" not in PROMPTS
    assert "rxn_chemistry_agent" not in CHAT_HANDLER
    assert not tool_path.exists()
