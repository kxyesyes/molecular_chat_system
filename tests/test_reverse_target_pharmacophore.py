import os
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import rdkit  # noqa: F401
    HAS_RDKIT = True
except ModuleNotFoundError:
    HAS_RDKIT = False


class PharmacophoreAlignmentTest(unittest.TestCase):
    def test_cache_dir_follows_reverse_target_data_dir_and_round_trips(self):
        import tempfile
        import src.reverse_target.pharmacophore_refiner as refiner

        previous_env = os.environ.get("REVERSE_TARGET_DATA_DIR")
        previous_cache_dir = refiner._CACHE_DIR
        with tempfile.TemporaryDirectory(prefix="pharm_cache_") as temp_dir:
            os.environ["REVERSE_TARGET_DATA_DIR"] = temp_dir
            refiner._CACHE_DIR = None
            try:
                cache_dir = refiner._get_cache_dir()
                payload = {"success": True, "features": [{"family": "Donor"}]}
                refiner._save_cache("CCO", payload)
                loaded = refiner._try_load_cache("CCO")
                temp_files = list(cache_dir.glob("*.tmp"))
            finally:
                refiner._CACHE_DIR = previous_cache_dir
                if previous_env is None:
                    os.environ.pop("REVERSE_TARGET_DATA_DIR", None)
                else:
                    os.environ["REVERSE_TARGET_DATA_DIR"] = previous_env

        self.assertEqual(cache_dir, Path(temp_dir) / "pharm3d_cache")
        self.assertEqual(loaded, payload)
        self.assertEqual(temp_files, [])

    def test_alignment_finds_best_pairs_when_same_family_order_differs(self):
        from src.reverse_target.pharmacophore_refiner import align_pharmacophore_features

        query = [
            {"family": "Donor", "pos": [0.0, 0.0, 0.0]},
            {"family": "Donor", "pos": [3.0, 0.0, 0.0]},
            {"family": "Acceptor", "pos": [0.0, 4.0, 0.0]},
            {"family": "Aromatic", "pos": [2.0, 3.0, 1.0]},
        ]
        hit = [
            {"family": "Donor", "pos": [20.0, 8.0, -2.0]},
            {"family": "Aromatic", "pos": [17.0, 7.0, -1.0]},
            {"family": "Acceptor", "pos": [16.0, 5.0, -2.0]},
            {"family": "Donor", "pos": [20.0, 5.0, -2.0]},
        ]

        result = align_pharmacophore_features(query, hit)

        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["score"], 0.95)
        self.assertLessEqual(result["alignment_rmsd"], 0.1)
        self.assertEqual(result["matched_pair_count"], 4)

    def test_alignment_score_is_invariant_to_rotation_and_translation(self):
        from src.reverse_target.pharmacophore_refiner import aligned_pharmacophore_score

        query = [
            {"family": "Aromatic", "pos": [0.0, 0.0, 0.0]},
            {"family": "Donor", "pos": [2.0, 0.0, 0.0]},
            {"family": "Acceptor", "pos": [0.0, 2.0, 0.0]},
        ]
        hit = [
            {"family": "Aromatic", "pos": [10.0, -5.0, 3.0]},
            {"family": "Donor", "pos": [10.0, -3.0, 3.0]},
            {"family": "Acceptor", "pos": [8.0, -5.0, 3.0]},
        ]

        score = aligned_pharmacophore_score(query, hit)

        self.assertGreaterEqual(score, 0.95)

    def test_refinement_timeout_logs_once_and_returns_all_candidates(self):
        from src.reverse_target.pharmacophore_refiner import refine_with_pharmacophore

        candidates = [
            {"target_name": f"T{i}", "canonical_smiles": "CCO", "final_similarity": 0.8 - i * 0.1}
            for i in range(3)
        ]

        with patch(
            "src.reverse_target.pharmacophore_refiner.get_molecule_pharmacophore",
            return_value={"success": True, "features": [], "feature_counts": {}},
        ):
            with self.assertLogs("src.reverse_target.pharmacophore_refiner", level="WARNING") as logs:
                refined = refine_with_pharmacophore(
                    "CCO",
                    candidates,
                    max_to_refine=3,
                    timeout_seconds=-1,
                )

        timeout_logs = [line for line in logs.output if "Pharmacophore refinement timeout reached" in line]
        self.assertEqual(len(timeout_logs), 1)
        self.assertEqual(len(refined), 3)
        self.assertTrue(
            all(row.get("pharm_refinement_status") == "timeout_fallback" for row in refined)
        )

    @unittest.skipUnless(HAS_RDKIT, "RDKit is not installed in this Python environment")
    def test_compute_pharm3d_score_returns_real_alignment_metadata(self):
        from src.reverse_target.pharmacophore_refiner import compute_pharm3d_score

        result = compute_pharm3d_score("CC(=O)Oc1ccccc1C(=O)O", "CC(=O)Oc1ccccc1C(=O)O")

        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["alignment_score"], 0.95)
        self.assertEqual(result["score_method"], "feature_point_kabsch_alignment")
        self.assertIsNotNone(result["alignment_rmsd"])
        self.assertGreaterEqual(len(result["alignment_pairs"]), 3)


class ReverseTargetPharm3DApiTest(unittest.TestCase):
    def setUp(self):
        self.previous_timeout = os.environ.get("REVERSE_TARGET_PHARM3D_TIMEOUT_SECONDS")
        os.environ["REVERSE_TARGET_PHARM3D_TIMEOUT_SECONDS"] = "0.05"
        self._previous_modules = {
            name: sys.modules.get(name)
            for name in ("src.reverse_target.predictor", "src.reverse_target.pharmacophore_refiner")
        }

    def tearDown(self):
        if self.previous_timeout is None:
            os.environ.pop("REVERSE_TARGET_PHARM3D_TIMEOUT_SECONDS", None)
        else:
            os.environ["REVERSE_TARGET_PHARM3D_TIMEOUT_SECONDS"] = self.previous_timeout

        for name, module in self._previous_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_predict_3d_timeout_returns_2d_fallback_results(self):
        predictor_module = types.ModuleType("src.reverse_target.predictor")

        class FakePredictor:
            def get_raw_similar_molecules(self, smiles, threshold, limit, organism_filter=""):
                return [
                    {
                        "target_name": "Demo target",
                        "final_similarity": 0.72,
                        "canonical_smiles": "CCO",
                    }
                ]

        predictor_module.get_predictor = lambda: FakePredictor()
        predictor_module._aggregate_by_target = (
            lambda rows, top_k, score_field: rows[:top_k]
        )
        sys.modules["src.reverse_target.predictor"] = predictor_module

        refiner_module = types.ModuleType("src.reverse_target.pharmacophore_refiner")

        def slow_refine(**kwargs):
            time.sleep(1.0)
            rows = kwargs["candidates"]
            for row in rows:
                row["final_3d_score"] = 0.99
            return rows

        refiner_module.refine_with_pharmacophore = slow_refine
        refiner_module.get_molecule_pharmacophore = lambda smiles: {
            "success": True,
            "features": [],
            "feature_counts": {},
            "properties": {},
        }
        sys.modules["src.reverse_target.pharmacophore_refiner"] = refiner_module

        from src.web.routes.api_routes import setup_api_routes

        app = FastAPI()
        setup_api_routes(app)
        response = TestClient(app).post(
            "/api/reverse_target/predict_3d",
            data={"smiles": "CCO", "threshold": "0.5", "top_k": "10", "max_refine": "1"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["pharmacophore_refinement_status"], "timeout")
        self.assertEqual(payload["results"][0]["final_3d_score"], 0.72)
        self.assertTrue(payload["message"])

    def test_predict_3d_decouples_return_count_from_refine_count(self):
        os.environ["REVERSE_TARGET_PHARM3D_TIMEOUT_SECONDS"] = "5"
        predictor_module = types.ModuleType("src.reverse_target.predictor")
        observed = {}

        class FakePredictor:
            def get_raw_similar_molecules(self, smiles, threshold, limit, organism_filter=""):
                observed["limit"] = limit
                return [
                    {
                        "target_name": f"Target {i}",
                        "final_similarity": 0.9 - i * 0.01,
                        "canonical_smiles": "CCO",
                    }
                    for i in range(limit)
                ]

        predictor_module.get_predictor = lambda: FakePredictor()
        predictor_module._aggregate_by_target = (
            lambda rows, top_k, score_field: rows[:top_k]
        )
        sys.modules["src.reverse_target.predictor"] = predictor_module

        refiner_module = types.ModuleType("src.reverse_target.pharmacophore_refiner")

        def fake_refine(**kwargs):
            observed["max_to_refine"] = kwargs["max_to_refine"]
            rows = kwargs["candidates"]
            for index, row in enumerate(rows):
                row["final_3d_score"] = row["final_similarity"]
                row["pharm_refinement_status"] = (
                    "refined" if index < kwargs["max_to_refine"] else "not_refined"
                )
            return rows

        refiner_module.refine_with_pharmacophore = fake_refine
        refiner_module.get_molecule_pharmacophore = lambda smiles: {
            "success": True,
            "features": [],
            "feature_counts": {},
            "properties": {},
        }
        sys.modules["src.reverse_target.pharmacophore_refiner"] = refiner_module

        from src.web.routes.api_routes import setup_api_routes

        app = FastAPI()
        setup_api_routes(app)
        response = TestClient(app).post(
            "/api/reverse_target/predict_3d",
            data={"smiles": "CCO", "threshold": "0.5", "top_k": "20", "max_refine": "5"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["count"], 20)
        self.assertEqual(observed["limit"], 400)
        self.assertEqual(observed["max_to_refine"], 5)
        self.assertEqual(payload["pharmacophore_refinement_status"], "partial")
        self.assertEqual(payload["candidate_pool_size"], 400)
        self.assertEqual(payload["raw_candidate_count"], 400)
        self.assertEqual(payload["unique_target_count_before_top_k"], 400)
        self.assertEqual(payload["requested_top_k"], 20)


if __name__ == "__main__":
    unittest.main()
