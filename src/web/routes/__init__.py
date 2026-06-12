"""
路由模块
"""
from .api_routes import setup_api_routes
from .page_routes import setup_page_routes
from .websocket_routes import setup_websocket_routes

__all__ = ['setup_api_routes', 'setup_page_routes', 'setup_websocket_routes']
