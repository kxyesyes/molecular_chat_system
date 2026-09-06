import unittest
from pathlib import Path
import os
import sys
import tempfile
import threading
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class TargetSearchDemoTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="target_search_test_")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _seeded_service(self):
        from src.target_search.seed import seed_database
        from src.target_search.service import TargetSearchService

        seed_database(project_root=self.root)
        return TargetSearchService(project_root=self.root)

    def test_connection_uses_concurrent_durable_sqlite_pragmas(self):
        from src.target_search.database import get_connection

        conn = get_connection(project_root=self.root)
        try:
            pragma = lambda name: next(iter(conn.execute(f"PRAGMA {name}").fetchone().values()))
            self.assertEqual(str(pragma("journal_mode")).lower(), "wal")
            self.assertEqual(int(pragma("synchronous")), 1)
            self.assertEqual(int(pragma("busy_timeout")), 30000)
            self.assertEqual(int(pragma("foreign_keys")), 1)
            self.assertNotEqual(str(pragma("locking_mode")).lower(), "exclusive")
        finally:
            conn.close()

    def test_target_runtime_paths_honor_environment_overrides(self):
        from src.target_search.database import (
            absolute_from_project,
            get_cache_dir,
            get_db_path,
        )

        with patch.dict(
            os.environ,
            {
                "TARGET_DB_PATH": "runtime/targets.sqlite",
                "TARGET_CACHE_DIR": "runtime/target-cache",
            },
            clear=False,
        ):
            self.assertEqual(
                get_db_path(self.root),
                (self.root / "runtime" / "targets.sqlite").resolve(),
            )
            self.assertEqual(
                get_cache_dir(self.root),
                (self.root / "runtime" / "target-cache").resolve(),
            )
            self.assertEqual(
                absolute_from_project(
                    "data/target_db/cache/rcsb/EGFR/4WKQ.cif",
                    self.root,
                ),
                (
                    self.root
                    / "runtime"
                    / "target-cache"
                    / "rcsb"
                    / "EGFR"
                    / "4WKQ.cif"
                ).resolve(),
            )

    def test_rebuild_deletes_the_configured_database_not_the_default(self):
        from src.target_search.database import get_connection
        from src.target_search.seed import rebuild_database, seed_database

        configured = self.root / "runtime" / "targets.sqlite"
        default = self.root / "data" / "target_db" / "target_database.sqlite"
        with patch.dict(
            os.environ,
            {"TARGET_DB_PATH": str(configured)},
            clear=False,
        ):
            seed_database(self.root)
            conn = get_connection(self.root)
            try:
                conn.execute(
                    "INSERT INTO targets (gene_symbol, uniprot_id) VALUES (?, ?)",
                    ("ONLY_BEFORE_REBUILD", "TEST-ONLY-BEFORE"),
                )
                conn.commit()
            finally:
                conn.close()

            rebuild_database(self.root)
            conn = get_connection(self.root)
            try:
                row = conn.execute(
                    "SELECT id FROM targets WHERE gene_symbol = ?",
                    ("ONLY_BEFORE_REBUILD",),
                ).fetchone()
            finally:
                conn.close()

        self.assertIsNone(row)
        self.assertTrue(configured.is_file())
        self.assertFalse(default.exists())

    def test_empty_search_returns_no_results(self):
        service = self._seeded_service()

        payload = service.search_targets("   ")

        self.assertEqual(
            payload,
            {
                "query": "",
                "results": [],
                "warnings": [],
                "lookup_path": [],
                "status": "not_found",
                "cache": {"target": "none", "structures": "none"},
            },
        )

    def test_seed_is_idempotent_and_searches_aliases_and_diseases(self):
        from src.target_search.seed import seed_database
        from src.target_search.service import TargetSearchService

        seed_database(project_root=self.root)
        seed_database(project_root=self.root)

        service = TargetSearchService(project_root=self.root)
        egfr = service.search_targets("HER1")["results"]
        cancer = service.search_targets("lung cancer")["results"]

        self.assertEqual(len(egfr), 1)
        self.assertEqual(egfr[0]["gene_symbol"], "EGFR")
        self.assertTrue(any(item["gene_symbol"] == "EGFR" for item in cancer))
        self.assertEqual(len(service.search_targets("EGFR")["results"]), 1)
        self.assertEqual(egfr[0]["match_reason"], "alias")
        self.assertEqual(cancer[0]["match_reason"], "disease")

    def test_search_filters_by_type_source_and_structure_flags(self):
        service = self._seeded_service()

        kinase = service.search_targets("cancer", target_type="Kinase")["results"]
        alphafold = service.search_targets("WDR5", source="AlphaFold")["results"]
        ligand = service.search_targets("WDR5", has_ligand=True)["results"]
        no_ligand = service.search_targets("MYC", has_ligand=False)["results"]

        self.assertTrue(kinase)
        self.assertTrue(all(item["target_type"] == "Kinase" for item in kinase))
        self.assertEqual([item["gene_symbol"] for item in alphafold], ["WDR5"])
        self.assertEqual([item["gene_symbol"] for item in ligand], ["WDR5"])
        self.assertEqual([item["gene_symbol"] for item in no_ligand], ["MYC"])

    def test_short_keyword_search_avoids_accidental_substring_hits(self):
        from src.target_search.seed import seed_database
        from src.target_search.service import TargetSearchService

        db_dir = self.root / "data" / "target_db"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "common_targets.csv").write_text(
            "gene_symbol,protein_name,uniprot_id,organism,target_type,description,function_summary,disease_keywords,chembl_target_id,aliases,pathway,known_drugs,representative_ligands,external_links\n"
            "OPRM1,Mu-type opioid receptor,P35372,Homo sapiens,GPCR,Opioid receptor,opioid signaling,pain;addiction,,MOR1,GPCR signaling,,,,\n",
            encoding="utf-8",
        )
        seed_database(project_root=self.root)
        service = TargetSearchService(project_root=self.root)

        ddi_results = service.search_targets("DDI")["results"]
        cyp_results = [item for item in ddi_results if item["gene_symbol"] == "CYP3A4"]
        oprm_results = [item for item in ddi_results if item["gene_symbol"] == "OPRM1"]

        self.assertTrue(cyp_results)
        self.assertFalse(oprm_results)

    def test_target_detail_includes_demo_enrichment_fields_and_external_links(self):
        service = self._seeded_service()
        egfr = service.search_targets("EGFR")["results"][0]

        detail = service.get_target_detail(egfr["target_id"])

        self.assertIn("pathway", detail)
        self.assertIn("known_drugs", detail)
        self.assertIn("representative_ligands", detail)
        self.assertIn("external_links", detail)
        self.assertTrue(any(link["label"] == "UniProt" for link in detail["external_links"]))
        self.assertTrue(detail["known_drugs"])

    def test_database_stats_counts_targets_structures_and_cache_files(self):
        service = self._seeded_service()
        target = service.search_targets("WDR5")["results"][0]
        structure = service.get_target_structures(target["target_id"])["structures"][0]
        cached_path = self.root / structure["local_file_path"]
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text("data_demo\n", encoding="utf-8")

        stats = service.get_database_stats()

        self.assertEqual(stats["target_count"], 15)
        self.assertEqual(stats["structure_count"], 18)
        self.assertEqual(stats["cached_structure_count"], 1)
        self.assertEqual(stats["missing_cache_count"], 17)
        self.assertEqual(stats["source_counts"]["RCSB_PDB"], 15)
        self.assertEqual(stats["source_counts"]["AlphaFold"], 3)
        self.assertTrue(stats["database_exists"])
        self.assertTrue(stats["cache_dir_exists"])

    def test_seed_loads_pde_csv_files_and_stats_include_pde_coverage(self):
        from src.target_search.seed import seed_database
        from src.target_search.service import TargetSearchService

        db_dir = self.root / "data" / "target_db"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "pde_targets.csv").write_text(
            "gene_symbol,protein_name,uniprot_id,organism,target_type,description,function_summary,disease_keywords,chembl_target_id,aliases,pathway,known_drugs,representative_ligands,external_links\n"
            "PDE5A,cGMP-specific phosphodiesterase,O76074,Homo sapiens,Phosphodiesterase,cGMP phosphodiesterase,cGMP signaling,pulmonary hypertension;erectile dysfunction,,PDE5;cGB-PDE,Cyclic nucleotide signaling,Sildenafil;Tadalafil,cGMP analogs,UniProt|https://www.uniprot.org/uniprotkb/O76074/entry\n",
            encoding="utf-8",
        )
        (db_dir / "pde_structures.csv").write_text(
            "gene_symbol,structure_id,source,structure_type,method,resolution,chain_ids,ligand_ids,organism,title,file_format,local_file_path,download_url,docking_recommended,is_preferred,is_downloaded,quality_note\n"
            "PDE5A,1T9R,RCSB_PDB,experimental,X-ray,2.3,A,SIL,Homo sapiens,PDE5A inhibitor complex,cif,data/target_db/cache/rcsb/PDE5A/1T9R.cif,https://files.rcsb.org/download/1T9R.cif,1,1,1,Local PDE seed structure.\n"
            "PDE5A,AF-O76074-F1,AlphaFold,predicted,Predicted,,A,,Homo sapiens,AlphaFold PDE5A model,cif,data/target_db/cache/alphafold/O76074/AF-O76074-F1-model_v4.cif,,0,0,1,Local AlphaFold PDE model.\n",
            encoding="utf-8",
        )
        rcsb_cache = self.root / "data/target_db/cache/rcsb/PDE5A/1T9R.cif"
        rcsb_cache.parent.mkdir(parents=True, exist_ok=True)
        rcsb_cache.write_text("pde5a rcsb\n", encoding="utf-8")

        seed_database(project_root=self.root)
        service = TargetSearchService(project_root=self.root)
        results = service.search_targets("PDE5")["results"]
        stats = service.get_database_stats()

        self.assertEqual(results[0]["gene_symbol"], "PDE5A")
        self.assertEqual(stats["pde_target_count"], 1)
        self.assertEqual(stats["pde_structure_count"], 2)
        self.assertEqual(stats["pde_cached_structure_count"], 1)
        self.assertEqual(stats["pde_cache_coverage"], 0.5)
        self.assertEqual(stats["alphafold_structure_count"], 4)
        self.assertEqual(stats["alphafold_cached_structure_count"], 0)

    def test_export_pde_csvs_writes_rebuildable_curated_files(self):
        from src.target_search.bulk_sync import TargetBulkSync, TargetSpec
        from src.target_search.export_curated import export_pde_csvs
        from src.target_search.seed import rebuild_database
        from src.target_search.service import TargetSearchService

        syncer = TargetBulkSync(project_root=self.root)
        spec = TargetSpec("PDE5A", "O76074", "Phosphodiesterase", "pulmonary hypertension", ("PDE5",), "pde")
        target_id = syncer.upsert_target(spec, {})
        syncer.upsert_alphafold_structure(target_id, spec, "cif")

        result = export_pde_csvs(project_root=self.root)
        rebuild_database(project_root=self.root)

        service = TargetSearchService(project_root=self.root)
        stats = service.get_database_stats()
        results = service.search_targets("PDE5")["results"]

        self.assertTrue(result["target_csv"].endswith("pde_targets.csv"))
        self.assertEqual(result["target_count"], 1)
        self.assertEqual(result["structure_count"], 1)
        self.assertEqual(results[0]["gene_symbol"], "PDE5A")
        self.assertEqual(stats["pde_target_count"], 1)
        self.assertEqual(stats["pde_structure_count"], 1)

    def test_database_health_reports_tables_and_missing_cache_files(self):
        service = self._seeded_service()

        health = service.get_database_health()

        self.assertEqual(health["status"], "warning")
        self.assertTrue(health["database_exists"])
        self.assertEqual(set(health["tables"].keys()), {"targets", "target_aliases", "target_structures"})
        self.assertTrue(all(health["tables"].values()))
        self.assertEqual(health["target_count"], 15)
        self.assertEqual(health["structure_count"], 18)
        self.assertEqual(health["missing_cache_count"], 18)
        self.assertGreater(len(health["missing_cache_files"]), 0)
        self.assertIn("AlphaFold", health["notes"][0])

    def test_structures_are_scored_and_sorted(self):
        from src.target_search.seed import seed_database
        from src.target_search.service import TargetSearchService

        seed_database(project_root=self.root)
        service = TargetSearchService(project_root=self.root)
        target = service.search_targets("CYP3A4")["results"][0]
        payload = service.get_target_structures(target["target_id"])
        structures = payload["structures"]

        self.assertGreaterEqual(len(structures), 2)
        self.assertGreaterEqual(structures[0]["score"], structures[1]["score"])
        self.assertIn("chain_ids", structures[0])
        self.assertIsInstance(structures[0]["ligand_ids"], list)
        self.assertIn("recommendation_reasons", structures[0])
        self.assertIn("recommendation_level", structures[0])
        self.assertIsInstance(structures[0]["warnings"], list)
        self.assertIn(structures[0]["docking_grade"], {"A", "B", "C"})
        self.assertIn("级", structures[0]["docking_grade_label"])

    def test_pde_overview_summarizes_family_and_grades(self):
        from src.target_search.seed import seed_database
        from src.target_search.service import TargetSearchService

        db_dir = self.root / "data" / "target_db"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "pde_targets.csv").write_text(
            "gene_symbol,protein_name,uniprot_id,organism,target_type,description,function_summary,disease_keywords,chembl_target_id,aliases,pathway,known_drugs,representative_ligands,external_links\n"
            "PDE5A,cGMP-specific phosphodiesterase,O76074,Homo sapiens,Phosphodiesterase,cGMP phosphodiesterase,cGMP signaling,pulmonary hypertension,,PDE5,Cyclic nucleotide signaling,Sildenafil,cGMP analogs,\n"
            "PDE6D,PDE delta,O43924,Homo sapiens,Phosphodiesterase regulator,Prenyl-binding PDE-related protein,Retinal trafficking,retinal disease,,PDED,Phototransduction,,,,\n",
            encoding="utf-8",
        )
        (db_dir / "pde_structures.csv").write_text(
            "gene_symbol,structure_id,source,structure_type,method,resolution,chain_ids,ligand_ids,organism,title,file_format,local_file_path,download_url,docking_recommended,is_preferred,is_downloaded,quality_note\n"
            "PDE5A,1T9R,RCSB_PDB,experimental,X-ray,2.3,A,SIL,Homo sapiens,PDE5A inhibitor complex,cif,data/target_db/cache/rcsb/PDE5A/1T9R.cif,,1,1,1,Local PDE seed structure.\n"
            "PDE6D,AF-O43924-F1,AlphaFold,predicted,Predicted,,A,,Homo sapiens,AlphaFold PDE6D model,cif,data/target_db/cache/alphafold/O43924/AF-O43924-F1-model_v4.cif,,0,0,1,Local AlphaFold PDE model.\n",
            encoding="utf-8",
        )

        seed_database(project_root=self.root)
        overview = TargetSearchService(project_root=self.root).get_pde_overview()
        classes = {item["gene_symbol"]: item["pde_target_class"] for item in overview["targets"]}
        best_grades = {item["gene_symbol"]: item["best_structure"]["docking_grade"] for item in overview["targets"]}

        self.assertEqual(overview["target_count"], 2)
        self.assertEqual(classes["PDE5A"], "catalytic PDE enzyme")
        self.assertEqual(classes["PDE6D"], "regulatory/accessory PDE-related protein")
        self.assertEqual(best_grades["PDE5A"], "A")
        self.assertEqual(best_grades["PDE6D"], "C")

    def test_local_download_returns_cached_file_without_network(self):
        from src.target_search.seed import seed_database
        from src.target_search.service import TargetSearchService

        seed_database(project_root=self.root)
        service = TargetSearchService(project_root=self.root)
        target = service.search_targets("WDR5")["results"][0]
        structure = service.get_target_structures(target["target_id"])["structures"][0]

        cached_path = self.root / structure["local_file_path"]
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text("data_demo\n", encoding="utf-8")

        prepared = service.prepare_structure_file(structure["id"], requested_format="cif")

        self.assertTrue(prepared["success"])
        self.assertEqual(Path(prepared["file_path"]).read_text(encoding="utf-8"), "data_demo\n")

    def test_missing_structure_raises_not_found(self):
        service = self._seeded_service()

        with self.assertRaisesRegex(ValueError, "Structure not found"):
            service.prepare_structure_file(999999, requested_format="cif")

    def test_alphafold_without_cache_returns_clear_error(self):
        from src.target_search.downloader import StructureDownloadError
        from src.target_search import downloader

        service = self._seeded_service()
        target = service.search_targets("WDR5")["results"][0]
        structures = service.get_target_structures(target["target_id"])["structures"]
        alphafold = next(item for item in structures if item["source"] == "AlphaFold")

        original_get = downloader.requests.get
        downloader.requests.get = lambda *args, **kwargs: (_ for _ in ()).throw(
            downloader.requests.RequestException("network unavailable")
        )
        try:
            with self.assertRaisesRegex(StructureDownloadError, "Failed to download AlphaFold"):
                service.prepare_structure_file(alphafold["id"], requested_format="cif")
        finally:
            downloader.requests.get = original_get

    def test_structure_download_streams_validates_and_atomically_replaces(self):
        from src.target_search import downloader as downloader_module

        final_path = self.root / "runtime-cache" / "rcsb" / "EGFR" / "4WKQ.cif"

        class FakeResponse:
            headers = {"Content-Length": "35"}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                self_outer.assertFalse(final_path.exists())
                yield b"data_4WKQ\n"
                yield b"_atom_site.id\nATOM\n"

        self_outer = self

        def fake_get(_url, *, timeout, stream):
            self.assertEqual(timeout, 30)
            self.assertTrue(stream)
            return FakeResponse()

        structure = {
            "id": 7,
            "structure_id": "4WKQ",
            "source": "RCSB_PDB",
            "file_format": "cif",
            "local_file_path": "data/target_db/cache/rcsb/EGFR/4WKQ.cif",
            "download_url": "https://files.rcsb.org/download/4WKQ.cif",
        }
        with patch.dict(
            os.environ,
            {"TARGET_CACHE_DIR": "runtime-cache"},
            clear=False,
        ), patch.object(
            downloader_module.requests,
            "get",
            side_effect=fake_get,
        ), patch.object(
            downloader_module.StructureDownloader,
            "_mark_downloaded",
        ) as mark_downloaded:
            prepared = downloader_module.StructureDownloader(
                self.root
            ).prepare_structure_file(structure)

        managed_path = Path(prepared["file_path"])
        self.assertTrue(managed_path.is_relative_to(self.root / "runtime-cache"))
        self.assertTrue(managed_path.is_file())
        self.assertIn(b"_atom_site.id", managed_path.read_bytes())
        self.assertFalse(list((self.root / "runtime-cache").rglob("*.tmp")))
        self.assertFalse(final_path.exists())
        mark_downloaded.assert_called_once()

    def test_invalid_or_oversized_download_never_publishes_cache_file(self):
        from src.target_search import downloader as downloader_module

        structure = {
            "id": 8,
            "structure_id": "BAD1",
            "source": "RCSB_PDB",
            "file_format": "cif",
            "local_file_path": "data/target_db/cache/rcsb/BAD/BAD1.cif",
            "download_url": "https://example.invalid/BAD1.cif",
        }
        final_path = self.root / structure["local_file_path"]

        class InvalidResponse:
            headers = {}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield b"<html>not a structure</html>"

        instance = downloader_module.StructureDownloader(self.root)
        with patch.object(
            downloader_module.requests,
            "get",
            return_value=InvalidResponse(),
        ), patch.object(instance, "_mark_downloaded") as mark_downloaded:
            with self.assertRaisesRegex(
                downloader_module.StructureDownloadError,
                "validation",
            ):
                instance.prepare_structure_file(structure)

        self.assertFalse(final_path.exists())
        self.assertFalse(list(final_path.parent.glob("*.tmp")))
        mark_downloaded.assert_not_called()

        class OversizedResponse(InvalidResponse):
            headers = {"Content-Length": "100"}

        with patch.object(
            downloader_module,
            "MAX_STRUCTURE_DOWNLOAD_BYTES",
            10,
        ), patch.object(
            downloader_module.requests,
            "get",
            return_value=OversizedResponse(),
        ):
            with self.assertRaisesRegex(
                downloader_module.StructureDownloadError,
                "maximum",
            ):
                instance.prepare_structure_file(structure)

        self.assertFalse(final_path.exists())

    def test_structure_preflight_reports_cache_and_docking_readiness(self):
        service = self._seeded_service()
        target = service.search_targets("WDR5")["results"][0]
        structure = service.get_target_structures(target["target_id"])["structures"][0]

        missing = service.preflight_structure(structure["id"], requested_format="cif")
        self.assertEqual(missing["cache_status"], "missing")
        self.assertFalse(missing["local_file_exists"])
        self.assertTrue(missing["can_download"])
        self.assertTrue(missing["needs_pdbqt_conversion"])

        cached_path = self.root / structure["local_file_path"]
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text("data_demo\n", encoding="utf-8")

        cached = service.preflight_structure(structure["id"], requested_format="cif")
        self.assertEqual(cached["cache_status"], "available")
        self.assertTrue(cached["local_file_exists"])
        self.assertEqual(cached["file_size_bytes"], cached_path.stat().st_size)
        self.assertIn(cached["docking_grade"], {"A", "B", "C"})

    def test_alphafold_preflight_warns_when_not_primary_docking_choice(self):
        service = self._seeded_service()
        target = service.search_targets("WDR5")["results"][0]
        alphafold = next(
            item for item in service.get_target_structures(target["target_id"])["structures"]
            if item["source"] == "AlphaFold"
        )

        preflight = service.preflight_structure(alphafold["id"], requested_format="cif")

        self.assertEqual(preflight["docking_grade"], "C")
        self.assertFalse(preflight["suitable_for_direct_docking"])
        self.assertTrue(any("AlphaFold" in warning for warning in preflight["warnings"]))

    def test_bulk_sync_target_sets_include_pde_family(self):
        from src.target_search.bulk_sync import target_specs

        pde_specs = target_specs("pde")
        common_specs = target_specs("common")
        genes = {spec.gene_symbol for spec in pde_specs}
        common_genes = {spec.gene_symbol for spec in common_specs}

        self.assertIn("PDE5A", genes)
        self.assertIn("PDE10A", genes)
        self.assertGreaterEqual(len(pde_specs), 20)
        self.assertIn("BTK", common_genes)
        self.assertIn("BRD4", common_genes)
        self.assertIn("KCNH2", common_genes)
        self.assertIn("MAPK1", common_genes)
        self.assertIn("PCSK9", common_genes)
        self.assertIn("CXCR4", common_genes)

    def test_bulk_sync_can_upsert_target_and_alphafold_structure_without_network(self):
        from src.target_search.bulk_sync import TargetBulkSync, TargetSpec
        from src.target_search.service import TargetSearchService

        syncer = TargetBulkSync(project_root=self.root)
        spec = TargetSpec("PDE5A", "O76074", "Phosphodiesterase", "pulmonary hypertension", ("PDE5",), "pde")
        target_id = syncer.upsert_target(spec, {})
        syncer.upsert_alphafold_structure(target_id, spec, "cif")

        service = TargetSearchService(project_root=self.root)
        result = service.search_targets("PDE5")["results"][0]
        structures = service.get_target_structures(result["target_id"])["structures"]

        self.assertEqual(result["gene_symbol"], "PDE5A")
        self.assertTrue(any(item["source"] == "AlphaFold" for item in structures))

    def test_send_to_docking_uses_readable_chinese_message(self):
        service = self._seeded_service()
        target = service.search_targets("WDR5")["results"][0]
        structure = service.get_target_structures(target["target_id"])["structures"][0]
        cached_path = self.root / structure["local_file_path"]
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text("data_demo\n", encoding="utf-8")

        payload = service.send_to_docking(structure["id"])

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["message"], "结构文件已准备好，后续可接入分子对接模块")
        self.assertEqual(payload["protein_file"], structure["local_file_path"])

    def test_route_returns_404_for_missing_structure(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.target_search.seed import seed_database
        from src.target_search import routes
        from src.target_search.service import TargetSearchService

        seed_database(project_root=self.root)
        original_service = routes.TargetSearchService
        routes.TargetSearchService = lambda: TargetSearchService(project_root=self.root)
        try:
            app = FastAPI()
            routes.setup_target_search_routes(app)
            client = TestClient(app)

            response = client.get("/api/target-db/structures/999999/download?format=cif")
        finally:
            routes.TargetSearchService = original_service

        self.assertEqual(response.status_code, 404)

    def test_structure_download_preparation_runs_in_worker_thread(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.concurrency import run_in_threadpool as starlette_run_in_threadpool
        from src.target_search import routes

        route_thread_ids = []
        service_thread_ids = []
        structure_file = self.root / "prepared.cif"
        structure_file.write_bytes(b"data_demo\n")

        async def recording_run_in_threadpool(func, *args, **kwargs):
            route_thread_ids.append(threading.get_ident())
            return await starlette_run_in_threadpool(func, *args, **kwargs)

        class FakeTargetSearchService:
            def prepare_structure_file(self, structure_db_id, requested_format):
                service_thread_ids.append(threading.get_ident())
                return {
                    "success": True,
                    "file_path": str(structure_file),
                    "file_format": requested_format,
                }

        with patch.object(
            routes,
            "TargetSearchService",
            return_value=FakeTargetSearchService(),
        ), patch.object(
            routes,
            "run_in_threadpool",
            new=recording_run_in_threadpool,
            create=True,
        ):
            app = FastAPI()
            routes.setup_target_search_routes(app)
            with TestClient(app) as client:
                response = client.get(
                    "/api/target-db/structures/1/download?format=cif"
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"data_demo\n")
        self.assertEqual(len(route_thread_ids), 1)
        self.assertEqual(len(service_thread_ids), 1)
        self.assertNotEqual(route_thread_ids[0], service_thread_ids[0])

    def test_search_route_keeps_event_loop_responsive_during_slow_lookup(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.target_search import routes

        lookup_started = threading.Event()
        release_lookup = threading.Event()
        ping_completed = threading.Event()
        responses = {}

        class SlowTargetSearchService:
            def search_targets(self, *args, **kwargs):
                lookup_started.set()
                release_lookup.wait(timeout=5)
                return {
                    "query": "EGFR",
                    "results": [],
                    "warnings": [],
                    "lookup_path": ["local"],
                    "status": "not_found",
                    "cache": {"target": "miss", "structures": "miss"},
                }

            def close(self):
                return None

        with patch.object(
            routes,
            "TargetSearchService",
            return_value=SlowTargetSearchService(),
        ):
            app = FastAPI()

            @app.get("/ping")
            async def ping():
                return {"ok": True}

            routes.setup_target_search_routes(app)
            with TestClient(app) as client:
                search_thread = threading.Thread(
                    target=lambda: responses.setdefault(
                        "search", client.get("/api/target-db/search?query=EGFR")
                    )
                )
                ping_thread = threading.Thread(
                    target=lambda: (
                        responses.setdefault("ping", client.get("/ping")),
                        ping_completed.set(),
                    )
                )
                search_thread.start()
                self.assertTrue(lookup_started.wait(timeout=2))
                ping_thread.start()
                try:
                    self.assertTrue(
                        ping_completed.wait(timeout=0.5),
                        "slow target lookup blocked an unrelated async endpoint",
                    )
                finally:
                    release_lookup.set()
                    search_thread.join(timeout=5)
                    ping_thread.join(timeout=5)

        self.assertEqual(responses["ping"].status_code, 200)
        self.assertEqual(responses["search"].status_code, 200)

    def test_target_search_route_closes_owned_service_on_shutdown(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.target_search import routes

        class ClosingService:
            def __init__(self):
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        service = ClosingService()
        with patch.object(routes, "TargetSearchService", return_value=service):
            app = FastAPI()
            routes.setup_target_search_routes(app)
            with TestClient(app):
                self.assertEqual(service.close_calls, 0)

        self.assertEqual(service.close_calls, 1)

    def test_preflight_route_returns_404_for_missing_structure(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.target_search.seed import seed_database
        from src.target_search import routes
        from src.target_search.service import TargetSearchService

        seed_database(project_root=self.root)
        original_service = routes.TargetSearchService
        routes.TargetSearchService = lambda: TargetSearchService(project_root=self.root)
        try:
            app = FastAPI()
            routes.setup_target_search_routes(app)
            client = TestClient(app)

            response = client.get("/api/target-db/structures/999999/preflight?format=cif")
        finally:
            routes.TargetSearchService = original_service

        self.assertEqual(response.status_code, 404)

    def test_stats_and_health_routes_return_database_summary(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.target_search.seed import seed_database
        from src.target_search import routes
        from src.target_search.service import TargetSearchService

        seed_database(project_root=self.root)
        original_service = routes.TargetSearchService
        routes.TargetSearchService = lambda: TargetSearchService(project_root=self.root)
        try:
            app = FastAPI()
            routes.setup_target_search_routes(app)
            client = TestClient(app)

            stats = client.get("/api/target-db/stats")
            health = client.get("/api/target-db/health")
            pde = client.get("/api/target-db/pde-overview")
        finally:
            routes.TargetSearchService = original_service

        self.assertEqual(stats.status_code, 200)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(pde.status_code, 200)
        self.assertEqual(stats.json()["target_count"], 15)
        self.assertEqual(health.json()["status"], "warning")

    def test_target_database_tool_preserves_remote_structures_and_aggregates_evidence(self):
        from src.agent.tools.target_database_tool import TargetDatabaseTool

        class FakeService:
            def search_targets(self, _query):
                return {
                    "status": "resolved",
                    "warnings": ["stale_authoritative_cache"],
                    "lookup_path": ["local", "UniProt", "RCSB_PDB"],
                    "results": [
                        {
                            "target_id": None,
                            "gene_symbol": "EGFR",
                            "protein_name": "Epidermal growth factor receptor",
                            "uniprot_id": "P00533",
                            "source": "UniProt",
                            "source_record_id": "P00533",
                            "source_url": "https://www.uniprot.org/uniprotkb/P00533/entry",
                            "retrieved_at": "2026-01-01T00:00:00+00:00",
                            "expires_at": "2026-01-31T00:00:00+00:00",
                            "stale": True,
                            "recommended_structures": [
                                {
                                    "structure_id": "1ABC",
                                    "source": "RCSB_PDB",
                                    "source_url": "https://www.rcsb.org/structure/1ABC",
                                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                                    "expires_at": "2026-01-08T00:00:00+00:00",
                                    "stale": True,
                                    "recommendation_level": "recommended",
                                },
                                {
                                    "structure_id": "AF-P00533-F1",
                                    "source": "AlphaFold",
                                    "source_url": "https://alphafold.ebi.ac.uk/entry/P00533",
                                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                                    "expires_at": "2026-01-08T00:00:00+00:00",
                                    "stale": True,
                                    "recommendation_level": "predicted",
                                },
                            ],
                        }
                    ],
                }

        tool = TargetDatabaseTool()
        tool._service = FakeService()

        result = tool.execute("EGFR")

        self.assertTrue(result["success"])
        self.assertEqual(
            result["data"][0]["recommended_structures"][0]["structure_id"],
            "1ABC",
        )
        self.assertEqual(
            result["lookup_path"], ["local", "UniProt", "RCSB_PDB"]
        )
        self.assertIn("stale_authoritative_cache", result["warnings"])
        self.assertEqual(result["quality"]["status"], "partial")
        self.assertEqual(
            result["evidence"],
            [
                {
                    "source": "UniProt",
                    "id": "P00533",
                    "url": "https://www.uniprot.org/uniprotkb/P00533/entry",
                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2026-01-31T00:00:00+00:00",
                    "stale": True,
                },
                {
                    "source": "RCSB_PDB",
                    "id": "1ABC",
                    "url": "https://www.rcsb.org/structure/1ABC",
                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2026-01-08T00:00:00+00:00",
                    "stale": True,
                },
                {
                    "source": "AlphaFold",
                    "id": "AF-P00533-F1",
                    "url": "https://alphafold.ebi.ac.uk/entry/P00533",
                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2026-01-08T00:00:00+00:00",
                    "stale": True,
                }
            ],
        )

    def test_target_database_tool_closes_and_releases_long_lived_service(self):
        from src.agent.tools.target_database_tool import TargetDatabaseTool

        class FakeService:
            def __init__(self):
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        tool = TargetDatabaseTool()
        service = FakeService()
        tool._service = service

        tool.close()
        tool.close()

        self.assertEqual(service.close_calls, 1)
        self.assertIsNone(tool._service)

    def test_target_database_tool_reports_zero_authoritative_matches_honestly(self):
        from src.agent.tools.target_database_tool import TargetDatabaseTool

        class FakeService:
            def search_targets(self, _query):
                return {
                    "status": "not_found",
                    "warnings": [],
                    "lookup_path": ["local", "UniProt"],
                    "results": [],
                }

        tool = TargetDatabaseTool()
        tool._service = FakeService()

        result = tool.execute("UNKNOWN")

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["quality"]["status"], "complete")
        self.assertEqual(result["lookup_path"], ["local", "UniProt"])
        self.assertIn("authoritative", result["formatted"].lower())
        self.assertNotIn("database is empty", result["formatted"].lower())

    def test_target_database_tool_requires_clarification_for_ambiguous_lookup(self):
        from src.agent.tools.target_database_tool import TargetDatabaseTool

        class FakeService:
            def search_targets(self, _query):
                return {
                    "status": "ambiguous",
                    "warnings": ["UniProt:ambiguous_match"],
                    "lookup_path": ["local", "UniProt"],
                    "results": [],
                }

        tool = TargetDatabaseTool()
        tool._service = FakeService()

        result = tool.execute("ABC1")

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["quality"]["status"], "partial")
        self.assertEqual(result["error"]["code"], "validation_error")
        self.assertIn("clarification", result["message"].lower())
        self.assertIn("UniProt:ambiguous_match", result["warnings"])

    def test_target_database_tool_reports_unavailable_without_conclusive_no_match(self):
        from src.agent.tools.base_tool import execute_tool_compat
        from src.agent.tools.target_database_tool import TargetDatabaseTool
        from src.agent.contracts import AgentErrorCode

        class FakeService:
            def search_targets(self, _query):
                return {
                    "status": "unavailable",
                    "warnings": ["UniProt:provider_unavailable"],
                    "lookup_path": ["local", "UniProt"],
                    "results": [],
                }

        tool = TargetDatabaseTool()
        tool._service = FakeService()

        raw = tool.execute("EGFR")
        adapted = execute_tool_compat(tool, "EGFR")

        self.assertFalse(raw["success"])
        self.assertEqual(raw["status"], "unavailable")
        self.assertEqual(raw["error"]["code"], "provider_error")
        self.assertNotIn("No matching targets", raw["message"])
        self.assertNotIn("conclusively", raw["formatted"].lower())
        self.assertFalse(adapted.success)
        self.assertEqual(adapted.error.code, AgentErrorCode.PROVIDER_ERROR)
        self.assertIn("UniProt:provider_unavailable", adapted.warnings)
        self.assertNotIn("provider body", str(adapted.to_legacy_dict()).lower())

    def test_target_database_tool_marks_mixed_results_and_outage_partial(self):
        from src.agent.tools.target_database_tool import TargetDatabaseTool

        class FakeService:
            def search_targets(self, query):
                if query == "EGFR":
                    return {
                        "status": "resolved",
                        "warnings": [],
                        "lookup_path": ["local", "UniProt", "RCSB_PDB"],
                        "results": [
                            {
                                "target_id": None,
                                "gene_symbol": "EGFR",
                                "uniprot_id": "P00533",
                                "recommended_structures": [],
                            }
                        ],
                    }
                return {
                    "status": "unavailable",
                    "warnings": ["UniProt:provider_unavailable"],
                    "lookup_path": ["local", "UniProt"],
                    "results": [],
                }

        tool = TargetDatabaseTool()
        tool._service = FakeService()

        result = tool.execute(["EGFR", "OUTAGE"])

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["quality"]["status"], "partial")
        self.assertEqual(
            result["quality"]["service_statuses"], ["resolved", "unavailable"]
        )
        self.assertIn("partial_authoritative_results", result["warnings"])
        self.assertIn("partial", result["message"].lower())

    def test_target_database_tool_formats_unavailable_structure_evidence_as_unknown(self):
        from src.agent.tools.target_database_tool import TargetDatabaseTool

        class FakeService:
            def search_targets(self, _query):
                return {
                    "status": "partial",
                    "warnings": ["authoritative_structures_unavailable"],
                    "lookup_path": ["local", "UniProt", "RCSB_PDB"],
                    "results": [
                        {
                            "target_id": None,
                            "gene_symbol": "EGFR",
                            "uniprot_id": "P00533",
                            "structure_count": None,
                            "has_experimental_structure": None,
                            "has_alphafold_structure": None,
                            "structure_evidence_status": "unavailable",
                            "recommended_structures": [],
                        }
                    ],
                }

        tool = TargetDatabaseTool()
        tool._service = FakeService()

        result = tool.execute("EGFR")

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "partial")
        self.assertIn("Structure evidence: unavailable", result["formatted"])
        self.assertIn("Experimental structure: unknown", result["formatted"])
        self.assertNotIn("Recommended structures: none recorded", result["formatted"])

    def test_target_database_tool_does_not_leak_provider_exception_text(self):
        from src.agent.tools.target_database_tool import TargetDatabaseTool

        class FakeService:
            def search_targets(self, _query):
                raise RuntimeError("provider body SECRET_TOKEN C:\\private\\targets.db")

        tool = TargetDatabaseTool()
        tool._service = FakeService()

        result = tool.execute("EGFR")

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "Target search is temporarily unavailable.")
        self.assertNotIn("SECRET_TOKEN", str(result))
        self.assertNotIn("private", str(result).lower())

    def test_target_database_tool_does_not_log_service_construction_details(self):
        from src.agent.tools.target_database_tool import TargetDatabaseTool

        with patch(
            "src.target_search.service.TargetSearchService",
            side_effect=RuntimeError("provider body SECRET_TOKEN C:\\private\\targets.db"),
        ), self.assertLogs(
            "src.agent.tools.target_database_tool", level="ERROR"
        ) as captured:
            result = TargetDatabaseTool().execute("EGFR")

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "Target search is temporarily unavailable.")
        self.assertNotIn("SECRET_TOKEN", "\n".join(captured.output))
        self.assertNotIn("private", "\n".join(captured.output).lower())


if __name__ == "__main__":
    unittest.main()
