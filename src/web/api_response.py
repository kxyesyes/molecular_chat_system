from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.responses import JSONResponse


def api_success(
    data: Any = None,
    message: str = "success",
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "success": True,
        "code": "OK",
        "message": message,
        "data": data,
        "request_id": request_id or str(uuid4()),
    }


def api_error(
    code: str,
    message: str,
    status_code: int = 400,
    details: Any = None,
    request_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id or str(uuid4()),
        },
    )
