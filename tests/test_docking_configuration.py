import tempfile
import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.docking.molecular_docking_service import MolecularDockingService


class DockingConfigurationTest(unittest.TestCase):
    def test_configure_accepts_direct_prepare_receptor_command(self):
        with tempfile.TemporaryDirectory(prefix="docking_config_") as tmp:
            root = Path(tmp)
            vina = root / "vina.exe"
            prepare_receptor = root / "prepare_receptor"
            prepare_ligand = root / "mk_prepare_ligand.py"
            for path in (vina, prepare_receptor, prepare_ligand):
                path.write_text("", encoding="utf-8")

            service = MolecularDockingService(
                {
                    "vina_exe": str(vina),
                    "prepare_receptor_cmd": str(prepare_receptor),
                    "prepare_ligand_cmd": str(prepare_ligand),
                }
            )

        self.assertEqual(service.vina_exe, str(vina.resolve()))
        self.assertEqual(service.prepare_receptor_cmd, str(prepare_receptor.resolve()))
        self.assertEqual(service.prepare_ligand_cmd, str(prepare_ligand.resolve()))


if __name__ == "__main__":
    unittest.main()
