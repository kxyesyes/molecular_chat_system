import os
import sys
import asyncio
import tempfile
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

    def test_transformed_alignment_uses_global_best_matching_not_greedy_order(self):
        from src.reverse_target.pharmacophore_refiner import _score_transformed_alignment
        import numpy as np

        query_features = [
            {"index": 0, "family": "Hydrophobe", "pos": np.array([0.0, 0.0, 0.0])},
            {"index": 1, "family": "Hydrophobe", "pos": np.array([1.0, 0.0, 0.0])},
            {"index": 2, "family": "Donor", "pos": np.array([0.0, 1.0, 0.0])},
        ]
        hit_features = [
            {"index": 0, "family": "Hydrophobe", "pos": np.array([0.9, 0.0, 0.0])},
            {"index": 1, "family": "Hydrophobe", "pos": np.array([0.0, 0.0, 0.0])},
            {"index": 2, "family": "Donor", "pos": np.array([0.0, 1.0, 0.0])},
        ]

        result = _score_transformed_alignment(
            query_features,
            hit_features,
            rotation=np.eye(3),
            hit_center=np.zeros(3),
            query_center=np.zeros(3),
            distance_cutoff=0.25,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["matched_pair_count"], 3)
        self.assertLess(result["alignment_rmsd"], 0.1)

    def test_family_matching_uses_bounded_search_for_high_symmetry_features(self):
        from src.reverse_target.pharmacophore_refiner import _best_family_matches
        import numpy as np

        query = [
            {"index": index, "family": "Donor", "pos": np.array([float(index), 0.0, 0.0])}
            for index in range(8)
        ]
        hit = [
            {"index": index, "family": "Donor", "aligned_pos": np.array([float(index), 0.0, 0.0])}
            for index in range(8)
        ]
        checks = 0

        def check():
            nonlocal checks
            checks += 1

        matched = _best_family_matches(query, hit, distance_cutoff=10.0, check=check)

        self.assertEqual(len(matched), 8)
        self.assertLess(checks, 5000)

    def test_alignment_reports_bounded_matching_method(self):
        from src.reverse_target.pharmacophore_refiner import _score_transformed_alignment
        import numpy as np

        features = [
            {"index": 0, "family": "Donor", "pos": np.array([0.0, 0.0, 0.0])},
            {"index": 1, "family": "Acceptor", "pos": np.array([1.0, 0.0, 0.0])},
            {"index": 2, "family": "Aromatic", "pos": np.array([0.0, 1.0, 0.0])},
        ]

        result = _score_transformed_alignment(
            features,
            [{**feature, "aligned_pos": feature["pos"]} for feature in features],
            rotation=np.eye(3),
            hit_center=np.zeros(3),
            query_center=np.zeros(3),
            distance_cutoff=0.25,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["matching_method"], "bounded_beam_search")

    def test_hydrophobe_alignment_allows_wider_cutoff_than_polar_features(self):
        from src.reverse_target.pharmacophore_refiner import _score_transformed_alignment
        import numpy as np

        query_features = [
            {"index": 0, "family": "Hydrophobe", "pos": np.array([0.0, 0.0, 0.0])},
        ]
        hit_features = [
            {"index": 0, "family": "Hydrophobe", "pos": np.array([2.25, 0.0, 0.0])},
        ]

        result = _score_transformed_alignment(
            query_features,
            hit_features,
            rotation=np.eye(3),
            hit_center=np.zeros(3),
            query_center=np.zeros(3),
            distance_cutoff=1.8,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["matched_pair_count"], 1)

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

    @unittest.skipUnless(HAS_RDKIT, "RDKit is not installed in this Python environment")
    def test_conformer_result_records_real_optimization_status(self):
        from src.reverse_target.pharmacophore_refiner import get_molecule_pharmacophore

        result = get_molecule_pharmacophore("CCO")
        self.assertTrue(result["success"])
        self.assertEqual(result["conformer_status"], "optimized")
        self.assertEqual(result["embedding_method"], "ETKDGv3")
        self.assertTrue(result["mmff_converged"])

    def test_lumped_hydrophobe_has_explicit_cutoff(self):
        from src.reverse_target.pharmacophore_refiner import _feature_distance_cutoff

        self.assertEqual(_feature_distance_cutoff("LumpedHydrophobe", 1.8), 3.0)

    @unittest.skipUnless(HAS_RDKIT, "RDKit is not installed in this Python environment")
    def test_empty_feature_extraction_is_not_a_scientific_success(self):
        from rdkit import Chem
        from src.reverse_target.pharmacophore_refiner import get_molecule_pharmacophore

        molecule = Chem.AddHs(Chem.MolFromSmiles("CC"))
        with patch(
            "src.reverse_target.pharmacophore_refiner._try_load_cache",
            return_value=None,
        ), patch(
            "src.reverse_target.pharmacophore_refiner.generate_3d_conformer_with_status",
            return_value=(molecule, {
                "conformer_status": "optimized",
                "embedding_method": "ETKDGv3",
                "mmff_converged": True,
                "conformer_energy": 0.0,
            }),
        ), patch(
            "src.reverse_target.pharmacophore_refiner.extract_pharmacophore_features",
            return_value=[],
        ):
            result = get_molecule_pharmacophore("CC")

        self.assertFalse(result["success"])
        self.assertIn("药效团特征", result["error"])

    def test_pharm3d_score_rejects_empty_feature_results(self):
        from src.reverse_target.pharmacophore_refiner import compute_pharm3d_score

        empty = {"success": True, "features": [], "feature_counts": {}}
        with patch(
            "src.reverse_target.pharmacophore_refiner.get_molecule_pharmacophore",
            return_value=empty,
        ):
            result = compute_pharm3d_score("CC", "CC")

        self.assertFalse(result["success"])
        self.assertIn("没有可比对的药效团特征", result["error"])

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
        from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore
        from src.web.routes import api_routes as support

        async def run_job(target, *args, **kwargs):
            if target is support._pharm3d_candidates_job:
                return [{
                    "target_name": "Demo target",
                    "final_similarity": 0.72,
                    "canonical_smiles": "CCO",
                }]
            raise asyncio.TimeoutError

        app = FastAPI()
        with tempfile.TemporaryDirectory() as directory:
            app.add_middleware(
                AgentSessionMiddleware,
                store=AgentSessionStore(Path(directory) / "sessions.sqlite"),
            )
            setup_api_routes(app)
            with patch.object(support, "_run_pharm3d_job", new=run_job):
                response = TestClient(app, base_url="http://localhost").post(
                    "/api/reverse_target/predict_3d",
                    data={"smiles": "CCO", "threshold": "0.5", "top_k": "10", "max_refine": "1"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["pharmacophore_refinement_status"], "timeout")
        self.assertIsNone(payload["results"][0]["final_3d_score"])
        self.assertEqual(payload["results"][0]["score_semantics"], "2d_similarity_fallback")
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
        from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore
        from src.web.routes import api_routes as support

        async def run_job(target, *args, **kwargs):
            if target is support._pharm3d_candidates_job:
                return FakePredictor().get_raw_similar_molecules(
                    args[0], args[1], args[2], args[3],
                )
            if target is support._pharm3d_refine_job:
                query_smiles, rows, max_to_refine = args[:3]
                return fake_refine(
                    query_smiles=query_smiles,
                    candidates=rows,
                    max_to_refine=max_to_refine,
                )
            return {"success": True, "features": [], "feature_counts": {}, "properties": {}}

        app = FastAPI()
        with tempfile.TemporaryDirectory() as directory:
            app.add_middleware(
                AgentSessionMiddleware,
                store=AgentSessionStore(Path(directory) / "sessions.sqlite"),
            )
            setup_api_routes(app)
            with patch.object(support, "_run_pharm3d_job", new=run_job):
                response = TestClient(app, base_url="http://localhost").post(
                    "/api/reverse_target/predict_3d",
                    data={"smiles": "CCO", "threshold": "0.5", "top_k": "20", "max_refine": "5"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["count"], 5)
        self.assertEqual(len(payload["fallback_results"]), 20)
        self.assertEqual(observed["limit"], 400)
        self.assertEqual(observed["max_to_refine"], 5)
        self.assertEqual(payload["pharmacophore_refinement_status"], "partial")
        self.assertEqual(payload["candidate_pool_size"], 400)
        self.assertEqual(payload["raw_candidate_count"], 400)
        self.assertEqual(payload["unique_target_count_before_top_k"], 400)
        self.assertEqual(payload["requested_top_k"], 20)

    def test_predict_3d_separates_refined_and_fallback_rankings(self):
        from src.web.routes import api_routes as support

        candidates = [
            {"target_name": "refined-target", "final_similarity": 0.61},
            {"target_name": "fallback-target", "final_similarity": 0.99},
        ]
        predictor_module = types.ModuleType("src.reverse_target.predictor")
        predictor_module.get_predictor = lambda: types.SimpleNamespace(
            get_raw_similar_molecules=lambda **kwargs: candidates
        )
        predictor_module._aggregate_by_target = (
            lambda rows, top_k, score_field: sorted(
                rows, key=lambda row: row.get(score_field) or 0, reverse=True
            )[:top_k]
        )
        sys.modules["src.reverse_target.predictor"] = predictor_module

        refiner_module = types.ModuleType("src.reverse_target.pharmacophore_refiner")

        def refine(**kwargs):
            return [
                dict(candidates[0], final_3d_score=0.7,
                     pharm_combined_3d=0.75, pharm_refinement_status="refined"),
                dict(candidates[1], final_3d_score=None,
                     pharm_combined_3d=0.88, pharm_similarity=0.91,
                     alignment_score=0.87, pharm_refinement_status="not_refined",
                     pharm_error="candidate limit")
            ]

        refiner_module.refine_with_pharmacophore = refine
        refiner_module.get_molecule_pharmacophore = lambda smiles: {
            "success": True, "features": [], "feature_counts": {}, "properties": {}
        }
        sys.modules["src.reverse_target.pharmacophore_refiner"] = refiner_module

        from src.web.routes.api_routes import setup_api_routes
        from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore

        async def run_job(target, *args, **kwargs):
            if target is support._pharm3d_candidates_job:
                return candidates
            if target is support._pharm3d_refine_job:
                return refine(query_smiles=args[0], candidates=args[1], max_to_refine=args[2])
            return {"success": True, "features": [], "feature_counts": {}, "properties": {}}

        app = FastAPI()
        with tempfile.TemporaryDirectory() as directory:
            app.add_middleware(
                AgentSessionMiddleware,
                store=AgentSessionStore(Path(directory) / "sessions.sqlite"),
            )
            setup_api_routes(app)
            with patch.object(support, "_run_pharm3d_job", new=run_job):
                with TestClient(app, base_url="http://localhost") as client:
                    response = client.post(
                        "/api/reverse_target/predict_3d",
                        data={"smiles": "CCO", "threshold": "0.5", "top_k": "2", "max_refine": "1"},
                    )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ranking_mode"], "3d_refined_with_2d_fallback")
        self.assertEqual([row["target_name"] for row in payload["results"]], ["refined-target"])
        self.assertEqual([row["target_name"] for row in payload["fallback_results"]], ["fallback-target"])
        self.assertIsNone(payload["fallback_results"][0]["final_3d_score"])
        self.assertIsNone(payload["fallback_results"][0]["pharm_combined_3d"])
        self.assertIsNone(payload["fallback_results"][0]["pharm_similarity"])
        self.assertIsNone(payload["fallback_results"][0]["alignment_score"])
        self.assertEqual(payload["fallback_count"], 1)


if __name__ == "__main__":
    unittest.main()
