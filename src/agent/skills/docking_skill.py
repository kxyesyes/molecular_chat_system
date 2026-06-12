"""Molecular docking skill for Agent routing."""

from .base_skill import BaseSkill


class DockingSkill(BaseSkill):
    name = "docking_simulation"
    description = (
        "当用户要求进行分子对接、计算结合能、评估配体与蛋白受体结合构象时使用。"
    )

    trigger_keywords = [
        "对接",
        "docking",
        "结合能",
        "亲和力",
        "vina",
        "autodock",
        "配体",
        "受体",
        "蛋白配体",
        "binding",
        "affinity",
        "receptor",
        "ligand",
    ]

    allowed_tools = [
        "molecular_docking",
        "prepare_receptor",
        "prepare_ligand",
        "run_docking",
        "get_docking_result",
    ]

    system_prompt = """# 分子对接专家

你负责把用户的自然语言对接请求转成可靠的 docking 工作流。

工作原则：
1. 必须确认配体和受体信息。配体可以是 SMILES 或结构文件，受体可以是 PDB/PDBQT 文件或靶点结构。
2. 如果缺少受体、配体或口袋坐标，不要编造结果，应先询问用户或调用靶点数据库辅助选择结构。
3. 优先使用结构化工具链：prepare_receptor -> prepare_ligand -> run_docking -> get_docking_result。
4. 输出时说明结合能、构象数量、输入文件、网格参数和任何失败原因。

红线：
- 不要凭空编造结合能。
- 不要在未实际运行 docking 时声称已经完成对接。
- 不要让 Agent 直接拼接底层命令行，应该调用 docking tools。
"""
