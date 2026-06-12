"""Agent-facing docking tools.

These wrappers keep Agent orchestration separate from the docking domain service.
The web API and Agent can share the same service without the Agent calling
command line scripts directly.
"""

from typing import Any, Dict, Optional

from .base_tool import BaseMolecularTool


class _DockingServiceTool(BaseMolecularTool):
    def __init__(self, name: str, description: str, service=None):
        super().__init__(name=name, description=description)
        self._service = service

    @property
    def service(self):
        if self._service is None:
            from src.docking import docking_service

            self._service = docking_service
        return self._service

    def should_use(self, query: str) -> bool:
        return any(word in query.lower() for word in ["docking", "vina", "对接", "配体", "受体"])

    def _result(self, success: bool, message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "query": "",
            "success": success,
            "message": message,
            "data": data or {},
            "formatted": message,
            "reasoning": message,
        }


class PrepareReceptorTool(_DockingServiceTool):
    def __init__(self, service=None):
        super().__init__(
            name="prepare_receptor",
            description="Prepare a protein receptor file for molecular docking.",
            service=service,
        )

    def execute(self, query: str) -> Dict[str, Any]:
        return self._result(False, "请通过结构化接口提供 receptor_path 和 output_path。")

    def run(self, receptor_path: str, output_path: str) -> Dict[str, Any]:
        ok = self.service.prepare_protein(receptor_path, output_path)
        return self._result(ok, "受体准备完成" if ok else "受体准备失败", {"output_path": output_path})


class PrepareLigandTool(_DockingServiceTool):
    def __init__(self, service=None):
        super().__init__(
            name="prepare_ligand",
            description="Prepare a ligand from SMILES or file for molecular docking.",
            service=service,
        )

    def execute(self, query: str) -> Dict[str, Any]:
        smiles_list = self.extract_smiles(query)
        if not smiles_list:
            return self._result(False, "请提供配体 SMILES 或通过结构化接口提供 ligand_path。")
        return self._result(False, "请通过结构化接口提供 output_path 后再准备配体。", {"smiles": smiles_list[0]})

    def run_from_smiles(self, smiles: str, output_path: str) -> Dict[str, Any]:
        ok = self.service.prepare_ligand_from_smiles(smiles, output_path)
        return self._result(ok, "配体准备完成" if ok else "配体准备失败", {"output_path": output_path})

    def run_from_file(self, ligand_path: str, output_path: str) -> Dict[str, Any]:
        ok = self.service.prepare_ligand_from_file(ligand_path, output_path)
        return self._result(ok, "配体准备完成" if ok else "配体准备失败", {"output_path": output_path})


class RunDockingTool(_DockingServiceTool):
    def __init__(self, service=None):
        super().__init__(
            name="run_docking",
            description="Run AutoDock Vina docking with prepared receptor and ligand PDBQT files.",
            service=service,
        )

    def execute(self, query: str) -> Dict[str, Any]:
        return self._result(False, "请通过结构化接口提供 receptor_path、ligand_path、grid 和 output_path。")

    def run(self, receptor_path: str, ligand_path: str, config, output_path: str) -> Dict[str, Any]:
        ok = self.service.run_vina_docking(receptor_path, ligand_path, config, output_path)
        return self._result(ok, "对接计算完成" if ok else "对接计算失败", {"output_path": output_path})


class GetDockingResultTool(_DockingServiceTool):
    def __init__(self, service=None):
        super().__init__(
            name="get_docking_result",
            description="Parse AutoDock Vina result files and return ranked poses.",
            service=service,
        )

    def execute(self, query: str) -> Dict[str, Any]:
        return self._result(False, "请通过结构化接口提供 Vina output_path。")

    def run(self, output_path: str) -> Dict[str, Any]:
        poses = self.service.parse_vina_results(output_path)
        return self._result(True, f"解析到 {len(poses)} 个对接构象", {"poses": [p.__dict__ for p in poses]})
