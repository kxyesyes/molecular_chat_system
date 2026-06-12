#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main page routes for Molecular Chat System."""

from typing import Optional

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates


def register_main_routes(
    app: FastAPI,
    templates: Optional[Jinja2Templates]
) -> None:
    """Register UI-facing page routes."""

    @app.get("/")
    async def home(request: Request):
        if templates is not None:
            return templates.TemplateResponse(
                request=request,
                name="index.html",
                context={},
            )
        return {"message": "Molecular Chat System API", "status": "running"}

    @app.get("/molecular-docking")
    async def molecular_docking(request: Request):
        if templates is not None:
            return templates.TemplateResponse(
                request=request,
                name="molecular_docking.html",
                context={},
            )
        return {"message": "Molecular Docking System", "status": "running"}

    # 为了兼容旧链接，将 /reverse-docking 也指向反向寻靶页面
    @app.get("/reverse-docking")
    async def reverse_docking_page(request: Request):
        if templates is not None:
            return templates.TemplateResponse(
                request=request,
                name="reverse_target.html",
                context={},
            )
        return {"message": "Reverse Target Prediction System", "status": "running"}

    @app.get("/reverse-target")
    async def reverse_target_page(request: Request):
        if templates is not None:
            return templates.TemplateResponse(
                request=request,
                name="reverse_target.html",
                context={},
            )
        return {"message": "Reverse Target Prediction System", "status": "running"}

    @app.get("/activity-prediction")
    async def activity_prediction_page(request: Request):
        if templates is not None:
            return templates.TemplateResponse(
                request=request,
                name="activity_prediction.html",
                context={},
            )
        return {"message": "Activity Prediction System", "status": "running"}

    @app.get("/kermt-admet")
    async def kermt_admet_page(request: Request):
        if templates is not None:
            return templates.TemplateResponse(
                request=request,
                name="kermt_admet.html",
                context={},
            )
        return {"message": "KERMT ADMET Prediction System", "status": "running"}

    @app.get("/molecular-design")
    async def molecular_design_page(request: Request):
        if templates is not None:
            return templates.TemplateResponse(
                request=request,
                name="molecular_design.html",
                context={},
            )
        return {"message": "Molecular Design System", "status": "running"}

    @app.get("/target-search")
    async def target_search_page(request: Request):
        if templates is not None:
            return templates.TemplateResponse(
                request=request,
                name="target_search.html",
                context={},
            )
        return {"message": "Target Search Demo", "status": "running"}

    @app.get("/cadd_interactive_radial.html")
    async def cadd_interactive_radial_page():
        html_path = Path(__file__).resolve().parents[3] / "cadd_interactive_radial.html"
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="cadd_interactive_radial.html not found")
        return FileResponse(html_path, media_type="text/html")
