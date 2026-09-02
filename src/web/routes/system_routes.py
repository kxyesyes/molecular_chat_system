from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from src.system import collect_data_versions
from src.task_runtime.database import get_task_db_path
from src.web.api_response import api_success


def _is_task_db_writable(task_db: Path) -> bool:
    if task_db.exists():
        return os.access(task_db, os.W_OK)
    return task_db.parent.exists() and os.access(task_db.parent, os.W_OK)


def setup_system_routes(app: FastAPI) -> None:
    @app.get("/api/system/data-versions")
    async def get_data_versions():
        return api_success(collect_data_versions())

    @app.get("/api/system/runtime")
    async def get_runtime_info():
        task_db = get_task_db_path()
        return api_success(
            {
                "task_runtime": {
                    "db": {
                        "name": task_db.name,
                        "type": "sqlite",
                        "location": (
                            "configured"
                            if os.environ.get("MEDCHAT_TASK_DB_PATH")
                            else "default"
                        ),
                        "exists": task_db.exists(),
                        "writable": _is_task_db_writable(task_db),
                    },
                }
            }
        )
