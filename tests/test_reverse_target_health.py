import pickle
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
import sys
from unittest.mock import patch
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import rdkit  # noqa: F401
    HAS_RDKIT = True
except ModuleNotFoundError:
    HAS_RDKIT = False


class ReverseTargetHealthTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="reverse_target_health_")
        self.data_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_minimal_database_files(self):
        (self.data_dir / "chembl_training_data.tsv").write_text(
            "molecule_chembl_id\tcanonical_smiles\ttarget_name\tstandard_type\tstandard_value\torganism\n"
            "CHEMBL25\tCCO\tDemo target\tIC50\t100\tHuman\n",
            encoding="utf-8",
        )
        (self.data_dir / "morgan_fingerprints.npy").write_bytes(b"morgan-demo")
        (self.data_dir / "maccs_fingerprints.npy").write_bytes(b"maccs-demo")
        with (self.data_dir / "fingerprint_metadata.pkl").open("wb") as handle:
            pickle.dump({"morgan_bits": 2048, "maccs_bits": 166}, handle)

    def test_health_reports_missing_files(self):
        from src.reverse_target.health import inspect_reverse_target_database

        health = inspect_reverse_target_database(self.data_dir)

        self.assertEqual(health["status"], "error")
        self.assertFalse(health["ready"])
        self.assertEqual(health["record_count"], 0)
        self.assertTrue(health["missing_files"])

    def test_health_reports_ready_database_without_loading_fingerprints(self):
        from src.reverse_target.health import inspect_reverse_target_database

        self._write_minimal_database_files()

        health = inspect_reverse_target_database(self.data_dir)

        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["ready"])
        self.assertEqual(health["record_count"], 1)
        self.assertEqual(health["metadata"]["morgan_bits"], 2048)
        self.assertEqual(health["metadata"]["maccs_bits"], 166)
        self.assertEqual(health["cache_dir"], str((self.data_dir / "pharm3d_cache").as_posix()))

    def test_health_route_uses_configured_data_dir(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.web.routes.api_routes import setup_api_routes

        self._write_minimal_database_files()
        previous = os.environ.get("REVERSE_TARGET_DATA_DIR")
        os.environ["REVERSE_TARGET_DATA_DIR"] = str(self.data_dir)
        try:
            app = FastAPI()
            setup_api_routes(app)
            response = TestClient(app).get("/api/reverse_target/health")
        finally:
            if previous is None:
                os.environ.pop("REVERSE_TARGET_DATA_DIR", None)
            else:
                os.environ["REVERSE_TARGET_DATA_DIR"] = previous

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])
        self.assertEqual(response.json()["record_count"], 1)

    def test_global_predictor_uses_configured_data_dir(self):
        import src.reverse_target.predictor as predictor_module
        from src.reverse_target.predictor import ReverseTargetPredictor

        previous_env = os.environ.get("REVERSE_TARGET_DATA_DIR")
        previous_predictor = predictor_module._predictor
        os.environ["REVERSE_TARGET_DATA_DIR"] = str(self.data_dir)
        predictor_module._predictor = None
        try:
            with patch.object(ReverseTargetPredictor, "load", return_value=None):
                predictor = predictor_module.get_predictor()
        finally:
            predictor_module._predictor = previous_predictor
            if previous_env is None:
                os.environ.pop("REVERSE_TARGET_DATA_DIR", None)
            else:
                os.environ["REVERSE_TARGET_DATA_DIR"] = previous_env

        self.assertEqual(predictor.data_dir, self.data_dir)

    @unittest.skipUnless(HAS_RDKIT, "RDKit is not installed in this Python environment")
    def test_predictor_organism_filter_keeps_human_rows(self):
        from src.reverse_target.predictor import ReverseTargetPredictor

        predictor = ReverseTargetPredictor(data_dir=self.data_dir)
        predictor.df = pd.DataFrame({"organism": ["Human", "Rattus norvegicus", "Homo sapiens"]})
        mask = np.array([True, True, True])

        filtered = predictor._apply_organism_filter(mask, "Human")

        self.assertEqual(filtered.tolist(), [True, False, True])

    @unittest.skipUnless(HAS_RDKIT, "RDKit is not installed in this Python environment")
    def test_predictor_load_is_thread_safe(self):
        from src.reverse_target.predictor import ReverseTargetPredictor

        for name in ("chembl_training_data.tsv", "morgan_fingerprints.npy", "maccs_fingerprints.npy"):
            (self.data_dir / name).write_text("placeholder", encoding="utf-8")

        predictor = ReverseTargetPredictor(data_dir=self.data_dir)
        read_count = 0
        read_lock = threading.Lock()

        def fake_read_csv(*args, **kwargs):
            nonlocal read_count
            with read_lock:
                read_count += 1
            time.sleep(0.05)
            return pd.DataFrame(
                {
                    "molecule_chembl_id": ["CHEMBL25"],
                    "canonical_smiles": ["CCO"],
                    "target_name": ["Demo target"],
                    "standard_type": ["IC50"],
                    "standard_value": [100],
                    "organism": ["Human"],
                }
            )

        def fake_np_load(path, mmap_mode=None):
            if str(path).endswith("morgan_fingerprints.npy"):
                return np.array([[1, 0, 1]], dtype=np.uint8)
            return np.array([[1, 1]], dtype=np.uint8)

        with patch("src.reverse_target.predictor.pd.read_csv", side_effect=fake_read_csv), patch(
            "src.reverse_target.predictor.np.load", side_effect=fake_np_load
        ):
            threads = [threading.Thread(target=predictor.load) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertTrue(predictor._loaded)
        self.assertEqual(read_count, 1)


class FingerprintConversionCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self._previous_modules = {
            name: sys.modules.get(name)
            for name in (
                "rdkit",
                "rdkit.Chem",
                "rdkit.DataStructs",
                "rdkit.Chem.AllChem",
                "rdkit.Chem.MACCSkeys",
                "src.reverse_target.predictor",
            )
        }

    def tearDown(self):
        for name, module in self._previous_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_bitvect_conversion_falls_back_when_rdkit_numpy_bridge_rejects_array(self):
        rdkit_module = types.ModuleType("rdkit")
        chem_module = types.ModuleType("rdkit.Chem")
        data_structs_module = types.ModuleType("rdkit.DataStructs")
        all_chem_module = types.ModuleType("rdkit.Chem.AllChem")
        maccs_module = types.ModuleType("rdkit.Chem.MACCSkeys")

        def failing_convert_to_numpy_array(bitvect, arr):
            raise ValueError("Expecting a Numeric array object")

        data_structs_module.ConvertToNumpyArray = failing_convert_to_numpy_array
        chem_module.AllChem = all_chem_module
        chem_module.MACCSkeys = maccs_module
        rdkit_module.Chem = chem_module
        rdkit_module.DataStructs = data_structs_module

        sys.modules["rdkit"] = rdkit_module
        sys.modules["rdkit.Chem"] = chem_module
        sys.modules["rdkit.DataStructs"] = data_structs_module
        sys.modules["rdkit.Chem.AllChem"] = all_chem_module
        sys.modules["rdkit.Chem.MACCSkeys"] = maccs_module
        sys.modules.pop("src.reverse_target.predictor", None)

        from src.reverse_target.predictor import _bitvect_to_numpy_array

        class FakeBitVect:
            def __init__(self, bits):
                self.bits = bits

            def GetNumBits(self):
                return len(self.bits)

            def GetBit(self, index):
                return self.bits[index]

        converted = _bitvect_to_numpy_array(FakeBitVect([1, 0, 1, 1]))

        self.assertEqual(converted.dtype, np.uint8)
        self.assertEqual(converted.tolist(), [1, 0, 1, 1])


if __name__ == "__main__":
    unittest.main()
