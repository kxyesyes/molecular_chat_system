import unittest
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class DockingAgentArchitectureTests(unittest.TestCase):
    def test_docking_domain_schemas_are_available(self):
        from src.docking.schemas import DockingRequest, DockingJobResult

        request = DockingRequest(
            receptor_path="protein.pdbqt",
            ligand_path="ligand.pdbqt",
            center=(1.0, 2.0, 3.0),
            size=(20.0, 20.0, 20.0),
        )
        result = DockingJobResult(job_id="job-1", success=True, output_path="out.pdbqt")

        self.assertEqual(request.center_x, 1.0)
        self.assertEqual(request.size_z, 20.0)
        self.assertTrue(result.success)

    def test_docking_service_exposes_adapters_after_configure(self):
        from src.docking.adapters import ADFRAdapter, MeekoAdapter, OpenBabelAdapter, VinaAdapter
        from src.docking.molecular_docking_service import MolecularDockingService

        service = MolecularDockingService(config={"vina_exe": "vina", "prepare_ligand_cmd": "mk_prepare_ligand"})

        self.assertIsInstance(service.vina_adapter, VinaAdapter)
        self.assertIsInstance(service.ligand_adapter, MeekoAdapter)
        self.assertIsInstance(service.receptor_adapter, ADFRAdapter)
        self.assertIsInstance(service.openbabel_adapter, OpenBabelAdapter)

    def test_agent_docking_tools_are_registered_and_compatible(self):
        from src.agent.tools.docking_tools import (
            PrepareLigandTool,
            PrepareReceptorTool,
            RunDockingTool,
        )
        from src.agent.tools import get_optional_tool

        tool = get_optional_tool("MolecularDocking")

        self.assertEqual(tool.name, "molecular_docking")
        self.assertEqual(PrepareLigandTool().name, "prepare_ligand")
        self.assertEqual(PrepareReceptorTool().name, "prepare_receptor")
        self.assertEqual(RunDockingTool().name, "run_docking")


if __name__ == "__main__":
    unittest.main()
