from __future__ import annotations

from fastapi import FastAPI

from src.system import collect_data_versions
from src.task_runtime.database import get_task_db_path, init_db
from src.web.api_response import api_success


def setup_system_routes(app: FastAPI) -> None:
    @app.get("/api/system/data-versions")
    async def get_data_versions():
        return api_success(collect_data_versions())

    @app.get("/api/system/runtime")
    async def get_runtime_info():
        task_db = init_db(get_task_db_path())
        return api_success(
            {
                "task_runtime": {
                    "db_path": task_db.as_posix(),
                    "exists": task_db.exists(),
                }
            }
        )
