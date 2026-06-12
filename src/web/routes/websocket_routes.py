"""
WebSocket 路由模块 - 聊天功能
"""
import json
import asyncio
import logging
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


def setup_websocket_routes(app, chat_handler):
    """设置 WebSocket 路由"""
    
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for chat"""
        await chat_handler.handle_websocket(websocket)
