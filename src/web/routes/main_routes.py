#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main page routes for Molecular Chat System."""

from inspect import signature
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

    explicit_request = (
        templates is not None
        and "request" in signature(templates.TemplateResponse).parameters
    )

    def render_template(request: Request, name: str):
        if explicit_request:
            return templates.TemplateResponse(request=request, name=name, context={})
        return templates.TemplateResponse(name=name, context={"request": request})

    @app.get("/")
    async def home(request: Request):
        if templates is not None:
            response = render_template(request, "index.html")
            # The homepage contains inline UI and configuration markup. Do not
            # let a local browser keep an older template after a code update.
            response.headers["Cache-Control"] = "no-store"
            return response
        return {"message": "Molecular Chat System API", "status": "running"}

    @app.get("/molecular-docking")
    async def molecular_docking(request: Request):
        if templates is not None:
            return render_template(request, "molecular_docking.html")
        return {"message": "Molecular Docking System", "status": "running"}

    # 为了兼容旧链接，将 /reverse-docking 也指向反向寻靶页面
    @app.get("/reverse-docking")
    async def reverse_docking_page(request: Request):
        if templates is not None:
            return render_template(request, "reverse_target.html")
        return {"message": "Reverse Target Prediction System", "status": "running"}

    @app.get("/reverse-target")
    async def reverse_target_page(request: Request):
        if templates is not None:
            return render_template(request, "reverse_target.html")
        return {"message": "Reverse Target Prediction System", "status": "running"}

    @app.get("/activity-prediction")
    async def activity_prediction_page(request: Request):
        if templates is not None:
            return render_template(request, "activity_prediction.html")
        return {"message": "Activity Prediction System", "status": "running"}

    @app.get("/admet")
    async def admet_page(request: Request):
        if templates is not None:
            return render_template(request, "admet.html")
        return {"message": "ADMET Research Console", "status": "running"}

    @app.get("/molecular-design")
    async def molecular_design_page(request: Request):
        if templates is not None:
            return render_template(request, "molecular_design.html")
        return {"message": "Molecular Design System", "status": "running"}

    @app.get("/target-search")
    async def target_search_page(request: Request):
        if templates is not None:
            return render_template(request, "target_search.html")
        return {"message": "Target Search Demo", "status": "running"}

    @app.get("/cadd_interactive_radial.html")
    async def cadd_interactive_radial_page():
        html_path = (
            Path(__file__).resolve().parents[1]
            / "static"
            / "knowledge"
            / "cadd_interactive_radial.html"
        )
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="cadd_interactive_radial.html not found")
        return FileResponse(html_path, media_type="text/html")
