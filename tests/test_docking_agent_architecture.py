import asyncio
import os
import tempfile
import threading
import unittest
import sys
from pathlib import Path
from unittest.mock import patch


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

    def test_direct_and_batch_docking_run_complete_awaitables_in_worker_threads(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.concurrency import run_in_threadpool as starlette_run_in_threadpool
        from src.web.routes import api_routes

        route_thread_ids = []
        service_thread_ids = []

        async def recording_run_in_threadpool(func, *args, **kwargs):
            route_thread_ids.append(threading.get_ident())
            return await starlette_run_in_threadpool(func, *args, **kwargs)

        class FakeDockingService:
            async def perform_docking(self, **kwargs):
                service_thread_ids.append(threading.get_ident())
                asyncio.get_running_loop()
                await asyncio.sleep(0)
                return {
                    "success": True,
                    "job_id": f"job-{len(service_thread_ids)}",
                    "best_pose": {"binding_energy": -7.0},
                    "total_poses": 1,
                    "warnings": [],
                }

        app = FastAPI()
        api_routes.setup_api_routes(app, docking_service=FakeDockingService())

        with patch.object(
            api_routes,
            "run_in_threadpool",
            new=recording_run_in_threadpool,
            create=True,
        ):
            with TestClient(app) as client:
                direct = client.post(
                    "/api/docking/submit",
                    files={"protein_file": ("protein.pdbqt", b"ATOM\n", "text/plain")},
                    data={"smiles": "CCO"},
                )
                batch = client.post(
                    "/api/docking/batch_submit",
                    files={"protein_file": ("protein.pdbqt", b"ATOM\n", "text/plain")},
                    data={"batch_smiles": "CCO ligand-one\nCCC ligand-two"},
                )

        self.assertEqual(direct.status_code, 200, direct.text)
        self.assertEqual(batch.status_code, 200, batch.text)
        self.assertEqual(len(service_thread_ids), 3)
        self.assertEqual(len(route_thread_ids), 3)
        self.assertTrue(all(worker_id not in route_thread_ids for worker_id in service_thread_ids))

    def test_utility_query_resource_bounds_return_422(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.web.routes.api_routes import setup_api_routes

        app = FastAPI()
        setup_api_routes(app)
        client = TestClient(app)

        invalid_urls = [
            "/api/utils/smiles_to_image?smiles=CCO&width=63&height=200",
            "/api/utils/smiles_to_image?smiles=CCO&width=300&height=2049",
            "/api/utils/mcs?smiles1=CCO&smiles2=CCC&width=63&height=260&timeout=3",
            "/api/utils/mcs?smiles1=CCO&smiles2=CCC&width=360&height=2049&timeout=3",
            "/api/utils/mcs?smiles1=CCO&smiles2=CCC&width=360&height=260&timeout=0",
            "/api/utils/mcs?smiles1=CCO&smiles2=CCC&width=360&height=260&timeout=31",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 422, response.text)

    def test_docking_report_rejects_aggregate_base64_payload_over_limit(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.web.routes import api_routes

        class FakeDockingService:
            def __init__(self, work_dir):
                self.work_dir = work_dir

            def parse_vina_results(self, result_file):
                return []

        with tempfile.TemporaryDirectory(prefix="docking_report_limit_") as work_dir:
            job_dir = Path(work_dir) / "docking_job-1"
            job_dir.mkdir()
            (job_dir / "result.pdbqt").write_text("MODEL 1\nENDMDL\n", encoding="utf-8")

            app = FastAPI()
            api_routes.setup_api_routes(app, docking_service=FakeDockingService(work_dir))

            with patch.dict(
                os.environ,
                {"MEDCHAT_DOCKING_REPORT_MAX_BASE64_BYTES": "10"},
            ), patch.object(api_routes.logger, "error") as error_log, patch.object(
                api_routes.logger,
                "warning",
            ) as warning_log:
                response = TestClient(app).post(
                    "/api/docking/report/job-1",
                    json={
                        "format": "md",
                        "viewer_png_base64": "ABCDEF",
                        "smiles_images": ["12345"],
                    },
                )

        self.assertEqual(response.status_code, 413, response.text)
        self.assertNotIn("ABCDEF", response.text)
        error_log.assert_not_called()
        warning_log.assert_not_called()

    def test_docking_report_rejects_invalid_image_payload_types(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.web.routes import api_routes

        class FakeDockingService:
            def __init__(self, work_dir):
                self.work_dir = work_dir

            def parse_vina_results(self, result_file):
                return []

        invalid_payloads = [
            {"format": "md", "smiles_images": "ABCDEFGHIJK"},
            {"format": "md", "smiles_images": ["ABC", 7]},
            {"format": "md", "viewer_png_base64": {"payload": "VIEWER_SECRET"}},
        ]

        with tempfile.TemporaryDirectory(prefix="docking_report_types_") as work_dir:
            job_dir = Path(work_dir) / "docking_job-1"
            job_dir.mkdir()
            (job_dir / "result.pdbqt").write_text("MODEL 1\nENDMDL\n", encoding="utf-8")

            app = FastAPI()
            api_routes.setup_api_routes(app, docking_service=FakeDockingService(work_dir))

            with patch.dict(
                os.environ,
                {"MEDCHAT_DOCKING_REPORT_MAX_BASE64_BYTES": "10"},
            ), patch.object(api_routes.logger, "error") as error_log, patch.object(
                api_routes.logger,
                "warning",
            ) as warning_log:
                with TestClient(app) as client:
                    for payload in invalid_payloads:
                        with self.subTest(payload_type=type(payload.get("smiles_images"))):
                            response = client.post(
                                "/api/docking/report/job-1",
                                json=payload,
                            )
                            self.assertEqual(response.status_code, 422, response.text)
                            self.assertNotIn("ABCDEFGHIJK", response.text)
                            self.assertNotIn("VIEWER_SECRET", response.text)

        error_log.assert_not_called()
        warning_log.assert_not_called()

    def test_activity_batch_preserves_upload_limit_without_calling_predictor(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.activity import predictor as predictor_module
        from src.web.routes.api_routes import setup_api_routes

        app = FastAPI()
        setup_api_routes(app)

        with patch.dict(os.environ, {"MEDCHAT_MAX_UPLOAD_BYTES": "4"}), patch.object(
            predictor_module,
            "get_predictor",
        ) as get_predictor:
            response = TestClient(app).post(
                "/api/activity/batch_predict",
                files={"file": ("smiles.txt", b"CCO\nCCC\n", "text/plain")},
            )

        self.assertEqual(response.status_code, 413, response.text)
        get_predictor.assert_not_called()

    def test_activity_training_rejects_oversized_upload_before_creating_temp_file(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.activity import trainer as trainer_module
        from src.web.routes.api_routes import setup_api_routes

        app = FastAPI()
        setup_api_routes(app)

        with patch.dict(
            os.environ,
            {
                "MEDCHAT_MAX_UPLOAD_BYTES": "4",
            },
        ), patch.object(tempfile, "NamedTemporaryFile") as named_temp_file, patch.object(
            trainer_module,
            "submit_training_job",
        ) as submit_training_job:
            response = TestClient(app).post(
                "/api/activity/train",
                files={"file": ("train.csv", b"smiles,y\nCCO,1\n", "text/csv")},
                data={"target_column": "y"},
            )

        self.assertEqual(response.status_code, 413, response.text)
        named_temp_file.assert_not_called()
        submit_training_job.assert_not_called()

    def test_activity_training_removes_closed_temp_file_when_submission_fails(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.activity import trainer as trainer_module
        from src.web.routes.api_routes import setup_api_routes

        class TrackingTempFile:
            def __init__(self, path):
                self.name = str(path)
                self._handle = open(path, "wb")

            @property
            def closed(self):
                return self._handle.closed

            def write(self, content):
                return self._handle.write(content)

            def close(self):
                self._handle.close()

        with tempfile.TemporaryDirectory(prefix="activity_training_cleanup_") as temp_dir:
            temp_path = Path(temp_dir) / "training.csv"
            tracked_temp_file = TrackingTempFile(temp_path)
            app = FastAPI()
            setup_api_routes(app)

            with patch.dict(
                os.environ,
                {
                    "MEDCHAT_MAX_UPLOAD_BYTES": "1024",
                },
            ), patch.object(
                tempfile,
                "NamedTemporaryFile",
                return_value=tracked_temp_file,
            ), patch.object(
                trainer_module,
                "submit_training_job",
                side_effect=RuntimeError("submission failed"),
            ):
                response = TestClient(app).post(
                    "/api/activity/train",
                    files={"file": ("train.csv", b"smiles,y\nCCO,1\n", "text/csv")},
                    data={"target_column": "y"},
                )

            self.assertEqual(response.status_code, 500, response.text)
            self.assertTrue(tracked_temp_file.closed)
            self.assertFalse(temp_path.exists())


if __name__ == "__main__":
    unittest.main()
