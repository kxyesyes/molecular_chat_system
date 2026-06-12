import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class TargetDatabaseValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="target_db_validation_")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_minimal_pde_seed(self):
        db_dir = self.root / "data" / "target_db"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "pde_targets.csv").write_text(
            "gene_symbol,protein_name,uniprot_id,organism,target_type,description,function_summary,disease_keywords,chembl_target_id,aliases,pathway,known_drugs,representative_ligands,external_links\n"
            "PDE5A,cGMP-specific phosphodiesterase,O76074,Homo sapiens,Phosphodiesterase,cGMP phosphodiesterase,cGMP signaling,pulmonary hypertension,,PDE5,Cyclic nucleotide signaling,Sildenafil,cGMP analogs,\n",
            encoding="utf-8",
        )
        (db_dir / "pde_structures.csv").write_text(
            "gene_symbol,structure_id,source,structure_type,method,resolution,chain_ids,ligand_ids,organism,title,file_format,local_file_path,download_url,docking_recommended,is_preferred,is_downloaded,quality_note\n"
            "PDE5A,1T9R,RCSB_PDB,experimental,X-ray,2.3,A,SIL,Homo sapiens,PDE5A inhibitor complex,cif,data/target_db/cache/rcsb/PDE5A/1T9R.cif,,1,1,1,Local PDE seed structure.\n",
            encoding="utf-8",
        )
        cached = self.root / "data/target_db/cache/rcsb/PDE5A/1T9R.cif"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text("cached pde5a\n", encoding="utf-8")

    def test_validation_reports_pde_coverage_and_cache_state(self):
        from src.target_search.seed import seed_database
        from src.target_search.validate import validate_target_database

        self._write_minimal_pde_seed()
        seed_database(project_root=self.root)

        report = validate_target_database(
            project_root=self.root,
            expected_pde_targets={"PDE5A", "PDE6D"},
        )

        self.assertEqual(report["status"], "warning")
        self.assertTrue(report["database_exists"])
        self.assertEqual(report["pde_coverage"]["present_expected_genes"], ["PDE5A"])
        self.assertEqual(report["pde_coverage"]["missing_expected_genes"], ["PDE6D"])
        self.assertEqual(report["cache"]["missing_count"], 18)
        self.assertIn("A", report["structure_quality"]["docking_grade_summary"])

    def test_validation_cli_outputs_json_and_strict_fails_on_missing_pde(self):
        from src.target_search.seed import seed_database

        self._write_minimal_pde_seed()
        seed_database(project_root=self.root)

        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "target_db" / "validate_target_db.py"),
            "--project-root",
            str(self.root),
            "--expected-pde",
            "PDE5A,PDE6D",
            "--strict",
            "--json",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, cwd=PROJECT_ROOT)

        self.assertEqual(completed.returncode, 1)
        report = json.loads(completed.stdout)
        self.assertEqual(report["pde_coverage"]["missing_expected_genes"], ["PDE6D"])

    def test_validation_route_returns_report(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.target_search.seed import seed_database
        from src.target_search import routes
        from src.target_search.service import TargetSearchService

        self._write_minimal_pde_seed()
        seed_database(project_root=self.root)
        original_service = routes.TargetSearchService
        original_validation = routes.validate_target_database
        routes.TargetSearchService = lambda: TargetSearchService(project_root=self.root)
        routes.validate_target_database = lambda **kwargs: original_validation(
            project_root=self.root,
            **kwargs,
        )
        try:
            app = FastAPI()
            routes.setup_target_search_routes(app)
            client = TestClient(app)

            response = client.get("/api/target-db/validation?expected_pde=PDE5A,PDE6D")
        finally:
            routes.TargetSearchService = original_service
            routes.validate_target_database = original_validation

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["pde_coverage"]["missing_expected_genes"], ["PDE6D"])


if __name__ == "__main__":
    unittest.main()
