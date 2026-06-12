"""FastAPI routes for the target-search demo."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from .downloader import StructureDownloadError
from .service import TargetSearchService
from .validate import validate_target_database

logger = logging.getLogger(__name__)


def setup_target_search_routes(app: FastAPI) -> None:
    service = TargetSearchService()

    @app.get("/api/target-db/stats")
    async def get_database_stats():
        try:
            return service.get_database_stats()
        except Exception as exc:
            logger.error("Get target database stats failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/target-db/health")
    async def get_database_health():
        try:
            return service.get_database_health()
        except Exception as exc:
            logger.error("Get target database health failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/target-db/validation")
    async def validate_database(
        expected_pde: str | None = Query(None),
        require_cache: bool = Query(False),
    ):
        try:
            expected_targets = _parse_expected_pde(expected_pde)
            return validate_target_database(
                expected_pde_targets=expected_targets,
                require_cache=require_cache,
            )
        except Exception as exc:
            logger.error("Validate target database failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/target-db/pde-overview")
    async def get_pde_overview(top_structures_per_target: int = Query(3, ge=0, le=10)):
        try:
            return service.get_pde_overview(top_structures_per_target=top_structures_per_target)
        except Exception as exc:
            logger.error("Get PDE overview failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/target-db/search")
    async def search_targets(
        query: str = Query(..., min_length=1),
        target_type: str | None = Query(None),
        source: str | None = Query(None),
        has_experimental: bool | None = Query(None),
        docking_recommended: bool | None = Query(None),
        has_ligand: bool | None = Query(None),
    ):
        try:
            return service.search_targets(
                query,
                target_type=target_type,
                source=source,
                has_experimental=has_experimental,
                docking_recommended=docking_recommended,
                has_ligand=has_ligand,
            )
        except Exception as exc:
            logger.error("Target search failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/target-db/targets/{target_id}")
    async def get_target_detail(target_id: int):
        try:
            return service.get_target_detail(target_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            logger.error("Get target detail failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/target-db/targets/{target_id}/structures")
    async def get_target_structures(target_id: int):
        try:
            return service.get_target_structures(target_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            logger.error("Get target structures failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/target-db/structures/{structure_db_id}/preflight")
    async def preflight_structure(structure_db_id: int, format: str = Query("cif")):
        try:
            return service.preflight_structure(structure_db_id, requested_format=format)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            logger.error("Structure preflight failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/target-db/structures/{structure_db_id}/download")
    async def download_structure(structure_db_id: int, format: str = Query("cif")):
        try:
            prepared = service.prepare_structure_file(structure_db_id, requested_format=format)
            file_path = Path(prepared["file_path"])
            media_type = "text/plain" if prepared.get("file_format") in {"cif", "pdb"} else "application/octet-stream"
            return FileResponse(path=file_path, filename=file_path.name, media_type=media_type)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except StructureDownloadError as exc:
            return JSONResponse(
                status_code=502,
                content={"success": False, "error": str(exc), "structure_db_id": structure_db_id},
            )
        except Exception as exc:
            logger.error("Structure download failed: %s", exc, exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": str(exc), "structure_db_id": structure_db_id},
            )

    @app.post("/api/target-db/structures/{structure_db_id}/send-to-docking")
    async def send_to_docking(structure_db_id: int):
        try:
            return service.send_to_docking(structure_db_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except StructureDownloadError as exc:
            return JSONResponse(
                status_code=502,
                content={"status": "error", "message": str(exc), "structure_db_id": structure_db_id},
            )
        except Exception as exc:
            logger.error("Send structure to docking failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))


def _parse_expected_pde(value: str | None) -> set[str] | None:
    if not value or not value.strip():
        return None
    return {item.strip().upper() for item in value.split(",") if item.strip()}
