import pickle
import os
import json
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
        (self.data_dir / "chembl_data_with_fps.tsv").write_text(
            "molecule_chembl_id\tcanonical_smiles\ttarget_name\tstandard_type\tstandard_value\torganism\n"
            "CHEMBL25\tCCO\tDemo target\tIC50\t100\tHuman\n",
            encoding="utf-8",
        )
        (self.data_dir / "chembl_training_data.tsv").write_text(
            "molecule_chembl_id\tcanonical_smiles\ttarget_name\tstandard_type\tstandard_value\torganism\n",
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

    def test_reverse_predict_and_batch_run_predictor_methods_in_worker_threads(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.concurrency import run_in_threadpool as starlette_run_in_threadpool
        from src.reverse_target import predictor as predictor_module
        from src.web.routes import api_routes

        route_thread_ids = []
        predictor_thread_events = []

        async def recording_run_in_threadpool(func, *args, **kwargs):
            route_thread_ids.append(threading.get_ident())
            return await starlette_run_in_threadpool(func, *args, **kwargs)

        class FakePredictor:
            def predict(self, **kwargs):
                predictor_thread_events.append(("predict", threading.get_ident()))
                return []

            def predict_batch(self, **kwargs):
                predictor_thread_events.append(("predict_batch", threading.get_ident()))
                return []

        fake_predictor = FakePredictor()

        def fake_get_predictor():
            predictor_thread_events.append(("get_predictor", threading.get_ident()))
            return fake_predictor

        app = FastAPI()
        api_routes.setup_api_routes(app)

        with patch.object(
            api_routes,
            "run_in_threadpool",
            new=recording_run_in_threadpool,
            create=True,
        ), patch.object(predictor_module, "get_predictor", new=fake_get_predictor):
            with TestClient(app) as client:
                direct = client.post(
                    "/api/reverse_target/predict",
                    data={"smiles": "CCO", "threshold": "0.6", "top_k": "10"},
                )
                batch = client.post(
                    "/api/reverse_target/batch_predict",
                    files={"file": ("smiles.txt", b"CCO\nCCC\n", "text/plain")},
                    data={"threshold": "0.6", "top_k": "10"},
                )

        self.assertEqual(direct.status_code, 200, direct.text)
        self.assertEqual(batch.status_code, 200, batch.text)
        self.assertEqual(len(route_thread_ids), 2)
        self.assertEqual(
            [event for event, _ in predictor_thread_events],
            ["get_predictor", "predict", "get_predictor", "predict_batch"],
        )
        for index in range(2):
            getter_thread_id = predictor_thread_events[index * 2][1]
            predict_thread_id = predictor_thread_events[index * 2 + 1][1]
            self.assertEqual(getter_thread_id, predict_thread_id)
            self.assertNotEqual(route_thread_ids[index], getter_thread_id)

    def test_reverse_batch_rejects_upload_over_configured_limit(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.reverse_target import predictor as predictor_module
        from src.web.routes.api_routes import setup_api_routes

        class FailIfCalledPredictor:
            def predict_batch(self, **kwargs):
                raise AssertionError("predict_batch must not run for an oversized upload")

        app = FastAPI()
        setup_api_routes(app)

        with patch.dict(os.environ, {"MEDCHAT_MAX_UPLOAD_BYTES": "4"}), patch.object(
            predictor_module,
            "get_predictor",
            return_value=FailIfCalledPredictor(),
        ):
            response = TestClient(app).post(
                "/api/reverse_target/batch_predict",
                files={"file": ("smiles.txt", b"CCO\nCCC\n", "text/plain")},
                data={"threshold": "0.6", "top_k": "10"},
            )

        self.assertEqual(response.status_code, 413, response.text)
        self.assertNotIn("CCO", response.text)

    @unittest.skipUnless(HAS_RDKIT, "RDKit is not installed in this Python environment")
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
    def test_build_components_share_configured_data_dir(self):
        from src.reverse_target.build_database import DatabaseBuilder
        from src.reverse_target.download_chembl import ChEMBLDownloader
        from src.reverse_target.fetch_chembl_api import ChEMBLAPIFetcher
        from src.reverse_target.generate_fingerprints import FingerprintGenerator

        training_data = self.data_dir / "chembl_training_data.tsv"
        training_data.write_text(
            "molecule_chembl_id\tcanonical_smiles\ttarget_name\tstandard_type\tstandard_value\torganism\n",
            encoding="utf-8",
        )

        with patch.dict(
            os.environ,
            {"REVERSE_TARGET_DATA_DIR": str(self.data_dir)},
        ):
            self.assertEqual(DatabaseBuilder().output_dir, self.data_dir)
            self.assertEqual(ChEMBLDownloader().output_dir, self.data_dir)
            self.assertEqual(ChEMBLAPIFetcher().output_dir, self.data_dir)
            self.assertEqual(FingerprintGenerator().input_file, training_data)

    def test_extractor_writes_outputs_to_configured_data_dir(self):
        from src.reverse_target.extract_clean_data import ChEMBLDataExtractor

        database_path = (
            self.data_dir / "vendor" / "chembl_36" / "chembl_36_sqlite" / "chembl_36.db"
        )
        database_path.parent.mkdir(parents=True)
        database_path.touch()

        with patch.dict(
            os.environ,
            {"REVERSE_TARGET_DATA_DIR": str(self.data_dir)},
        ):
            extractor = ChEMBLDataExtractor(db_path=database_path)

        self.assertEqual(extractor.output_dir, self.data_dir)

    def test_config_discovers_extracted_chembl_database(self):
        from src.reverse_target.config import get_chembl_db_path

        database_path = self.data_dir / "chembl_36" / "chembl_36_sqlite" / "chembl_36.db"
        database_path.parent.mkdir(parents=True)
        database_path.touch()

        with patch.dict(
            os.environ,
            {"REVERSE_TARGET_DATA_DIR": str(self.data_dir)},
        ):
            resolved = get_chembl_db_path()

        self.assertEqual(resolved, database_path)

    def test_health_prefers_aligned_training_data_and_metadata_record_count(self):
        from src.reverse_target.health import inspect_reverse_target_database

        self._write_minimal_database_files()
        (self.data_dir / "reverse_target_metadata.json").write_text(
            json.dumps({"record_count": 12345, "source": "test"}),
            encoding="utf-8",
        )

        with patch("src.reverse_target.health._count_tsv_records", side_effect=AssertionError("should not scan")):
            health = inspect_reverse_target_database(self.data_dir)

        self.assertTrue(health["ready"])
        self.assertEqual(health["record_count"], 12345)
        self.assertTrue(health["files"]["training_data"]["path"].endswith("chembl_data_with_fps.tsv"))

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

    @unittest.skipUnless(HAS_RDKIT, "RDKit is not installed in this Python environment")
    def test_predictor_prefers_aligned_data_and_validates_fingerprint_rows(self):
        from src.reverse_target.predictor import ReverseTargetPredictor

        (self.data_dir / "chembl_data_with_fps.tsv").write_text(
            "molecule_chembl_id\tcanonical_smiles\ttarget_name\tstandard_type\tstandard_value\torganism\n"
            "CHEMBL1\tCCO\tTarget A\tIC50\t1\tHuman\n"
            "CHEMBL2\tCCC\tTarget B\tIC50\t2\tHuman\n",
            encoding="utf-8",
        )
        (self.data_dir / "chembl_training_data.tsv").write_text(
            "molecule_chembl_id\tcanonical_smiles\ttarget_name\tstandard_type\tstandard_value\torganism\n"
            "CHEMBL0\tC\tWrong target\tIC50\t9\tHuman\n",
            encoding="utf-8",
        )
        (self.data_dir / "morgan_fingerprints.npy").write_bytes(b"placeholder")
        (self.data_dir / "maccs_fingerprints.npy").write_bytes(b"placeholder")

        predictor = ReverseTargetPredictor(data_dir=self.data_dir)

        def fake_np_load(path, mmap_mode=None):
            name = Path(path).name
            if name == "morgan_fingerprints.npy":
                return np.array([[1, 0, 1]], dtype=np.uint8)
            if name == "maccs_fingerprints.npy":
                return np.array([[1, 1]], dtype=np.uint8)
            return np.array([2], dtype=np.uint16)

        with patch("src.reverse_target.predictor.np.load", side_effect=fake_np_load):
            with self.assertRaisesRegex(ValueError, "row count mismatch"):
                predictor.load()

        self.assertEqual(predictor.training_data_path.name, "chembl_data_with_fps.tsv")

    @unittest.skipUnless(HAS_RDKIT, "RDKit is not installed in this Python environment")
    def test_similarity_combination_uses_morgan_weight(self):
        from src.reverse_target.predictor import ReverseTargetPredictor

        predictor = ReverseTargetPredictor(data_dir=self.data_dir)
        combined = predictor._combine_similarity_scores(
            np.array([1.0, 0.0], dtype=float),
            np.array([0.0, 1.0], dtype=float),
        )

        self.assertGreater(combined[0], combined[1])

    @unittest.skipUnless(HAS_RDKIT, "RDKit is not installed in this Python environment")
    def test_raw_candidate_selection_is_target_diverse(self):
        from src.reverse_target.predictor import ReverseTargetPredictor

        predictor = ReverseTargetPredictor(data_dir=self.data_dir)
        predictor.df = pd.DataFrame(
            {
                "target_name": ["A", "A", "A", "B", "C"],
                "organism": ["Human"] * 5,
                "molecule_chembl_id": [f"CHEMBL{i}" for i in range(5)],
                "canonical_smiles": ["CCO"] * 5,
                "standard_type": ["IC50"] * 5,
                "standard_value": [1, 2, 3, 4, 5],
            }
        )
        final_sims = np.array([0.99, 0.98, 0.97, 0.96, 0.95], dtype=float)

        selected = predictor._select_diverse_candidate_indices(np.arange(5), final_sims, limit=3)

        self.assertEqual(selected.tolist(), [0, 3, 4])


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
