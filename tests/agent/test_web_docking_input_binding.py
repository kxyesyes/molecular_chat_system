from src.agent.supervisor import SupervisorAgent


PROMPT = (
    "请进行真实 docking。"
    "receptor: D:/work/data/receptor.pdb；"
    "ligand: D:/work/data/ligand.sdf；"
    "center=[5.99,3.01,17.345]；size=[20,20,20]。"
)


class _RecordingDockingTool:
    name = "molecular_docking"

    def __init__(self):
        self.received = None

    def execute(self, query, **kwargs):
        self.received = query
        return {
            "success": False,
            "message": "test-only docking boundary",
            "data": None,
            "formatted": "",
        }


def test_web_style_docking_prompt_is_bound_to_structured_tool_input():
    tool = _RecordingDockingTool()
    result = SupervisorAgent(tools={"molecular_docking": tool}).execute(
        PROMPT,
        active_skill="docking_simulation",
    )

    assert tool.received == {
        "receptor_path": "D:/work/data/receptor.pdb",
        "ligand_path": "D:/work/data/ligand.sdf",
        "center": [5.99, 3.01, 17.345],
        "size": [20.0, 20.0, 20.0],
    }


def test_incomplete_docking_prompt_remains_unstructured_for_safe_rejection():
    tool = _RecordingDockingTool()
    SupervisorAgent(tools={"molecular_docking": tool}).execute(
        "请把 CCO 和 EGFR docking，receptor: D:/work/data/receptor.pdb。",
        active_skill="docking_simulation",
    )

    assert isinstance(tool.received, str)
