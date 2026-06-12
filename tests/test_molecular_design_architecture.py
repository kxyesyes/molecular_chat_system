import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class DummyDesignModel:
    def generate(self, prompt, temperature=0.3, max_tokens=800):
        return "<script>alert('x')</script>\nUse [*]C as a simple fragment."


class MolecularDesignArchitectureTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="molecular_design_")
        self.data_dir = Path(self.temp_dir.name)
        self.fragment_csv = self.data_dir / "fragments_labeled.csv"
        self.fragment_csv.write_text(
            "fragment_smiles,frequency,label_lipophilic,label_hydrophilic,label_has_aromatic_ring\n"
            "[*]C,10,1,0,0\n"
            "[*]O,8,0,1,0\n"
            "[*]c1ccccc1,6,1,0,1\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _client(self):
        import src.web.routes.design_routes as design_routes

        design_routes._FRAG_DB_PATH = str(self.fragment_csv)
        design_routes._SAVE_DIR = str(self.data_dir / "saved")
        design_routes._frag_df = None

        app = FastAPI()
        design_routes.setup_design_routes(app, model=DummyDesignModel())
        return TestClient(app)

    def test_fragment_repository_clamps_pagination(self):
        from src.molecular_design.fragments import FragmentRepository

        repo = FragmentRepository(self.fragment_csv)

        result = repo.query(page=0, page_size=500)

        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 100)
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["fragments"]), 3)

    def test_invalid_smiles_properties_returns_400(self):
        response = self._client().post("/api/design/properties", json={"smiles": "not-a-smiles"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["success"], False)
        self.assertIn("SMILES", response.json()["error"])

    def test_unmarked_aromatic_parent_can_be_auto_marked_for_substitution(self):
        response = self._client().post(
            "/api/design/substitute",
            json={"parent_smiles": "c1ccccc1", "fragment_smiles": "[*]C"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["new_smiles"], "Cc1ccccc1")

    def test_ai_recommendation_escapes_model_html(self):
        response = self._client().post(
            "/api/design/ai_recommend",
            json={"command": "提高 QED", "current_smiles": "CCO", "current_props": {}},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertIn("&lt;script&gt;", payload["reply"])
        self.assertNotIn("<script>", payload["reply"])

    def test_empty_ai_recommendation_command_returns_400(self):
        response = self._client().post(
            "/api/design/ai_recommend",
            json={"command": "", "current_smiles": "CCO", "current_props": {}},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_save_molecule_rejects_invalid_smiles(self):
        response = self._client().post(
            "/api/design/save_molecule",
            json={"smiles": "not-a-smiles", "properties": {}},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_properties_route_runs_calculation_in_threadpool(self):
        import src.web.routes.design_routes as design_routes

        calls = []

        async def fake_run_in_threadpool(func, *args, **kwargs):
            calls.append(getattr(func, "__name__", "unknown"))
            return func(*args, **kwargs)

        with mock.patch.object(design_routes, "run_in_threadpool", fake_run_in_threadpool, create=True):
            response = self._client().post("/api/design/properties", json={"smiles": "CCO"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("calculate_properties", calls)

    def test_design_page_exposes_feedback_and_comparison_surfaces(self):
        template = (PROJECT_ROOT / "src/web/templates/molecular_design.html").read_text(encoding="utf-8")
        config_js = (PROJECT_ROOT / "src/web/static/js/design/config.js").read_text(encoding="utf-8")
        main_js = (PROJECT_ROOT / "src/web/static/js/design/main.js").read_text(encoding="utf-8")
        editor_js = (PROJECT_ROOT / "src/web/static/js/design/molecule_editor.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "src/web/static/css/molecular_design.css").read_text(encoding="utf-8")

        self.assertIn('id="designFeedback"', template)
        self.assertIn('id="candidateCompare"', template)
        self.assertIn("候选分子对比", template)
        self.assertIn("candidates: []", config_js)
        self.assertIn("addCandidateComparison", editor_js)
        self.assertIn("renderCandidateComparison", editor_js)
        self.assertIn("restoreCandidate", editor_js)
        self.assertIn("setFeedback", main_js)
        self.assertIn(".candidate-compare", css)
        self.assertIn(".candidate-row", css)

    def test_design_workspace_uses_shared_outer_spacing_and_height(self):
        css = (PROJECT_ROOT / "src/web/static/css/molecular_design.css").read_text(encoding="utf-8")
        template = (PROJECT_ROOT / "src/web/templates/molecular_design.html").read_text(encoding="utf-8")

        self.assertIn("--design-workspace-offset", css)
        self.assertIn("--design-workspace-min-height", css)
        self.assertIn("--design-console-height", css)
        self.assertIn('class="optimization-console"', template)
        self.assertIn(".optimization-console", css)
        self.assertIn("grid-template-columns: minmax(260px, 1fr) minmax(320px, 1.2fr) minmax(240px, 0.9fr)", css)
        self.assertIn("margin-top: var(--design-workspace-offset)", css)
        self.assertIn("min-height: var(--design-workspace-min-height)", css)
        self.assertIn("height: var(--design-console-height)", css)
        self.assertIn("--design-workspace-offset: 0px", css)

if __name__ == "__main__":
    unittest.main()
