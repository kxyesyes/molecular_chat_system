"""Legacy page-route compatibility layer."""

import logging
import os

from fastapi.templating import Jinja2Templates

from .main_routes import register_main_routes

logger = logging.getLogger(__name__)

_MAIN_PAGE_PATHS = {
    "/",
    "/molecular-docking",
    "/reverse-docking",
    "/reverse-target",
    "/activity-prediction",
    "/kermt-admet",
    "/molecular-design",
    "/target-search",
}


def setup_page_routes(app, base_dir: str):
    """Register page routes while preserving the old entrypoint."""
    templates = None
    template_dir = os.path.join(base_dir, "templates")
    if os.path.exists(template_dir):
        templates = Jinja2Templates(directory=template_dir)
        logger.info(f"Templates directory: {template_dir}")
    else:
        alt_templates = "src/web/templates"
        if os.path.exists(alt_templates):
            templates = Jinja2Templates(directory=alt_templates)
            logger.info(f"Templates directory: {alt_templates}")

    existing_paths = {getattr(route, "path", None) for route in app.routes}
    if not existing_paths & _MAIN_PAGE_PATHS:
        register_main_routes(app, templates)
    else:
        logger.info("Page routes already exist, skipping duplicate registration")

    if "/health" not in existing_paths:
        @app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {
                "status": "ok",
                "timestamp": __import__("time").time(),
            }
