"""AI 狼人杀 — WebSocket 连接管理器

职责：管理 WebSocket 连接，实时推送游戏事件到前端
技术方案 §四 对应实现

连接地址: ws://host/ws/game/{game_id}

消息格式（服务端推送）：
{
    "type": "事件类型",
    "data": { ... },
    "timestamp": "ISO8601"
}

调用链：record_event → ws_manager.broadcast → WebSocket → 前端
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket 连接管理器

    按 game_id 分组管理连接，支持一对多广播

    使用方式：
        manager = ConnectionManager()

        @app.websocket("/ws/game/{game_id}")
        async def ws_endpoint(websocket, game_id):
            await manager.connect(websocket, game_id)
            try:
                while True:
                    await websocket.receive_text()  # 保持连接
            except WebSocketDisconnect:
                manager.disconnect(websocket, game_id)
    """

    def __init__(self):
        # {game_id: [WebSocket, WebSocket, ...]}
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, game_id: str):
        """接受 WebSocket 连接并注册到指定对局"""
        await websocket.accept()
        if game_id not in self._connections:
            self._connections[game_id] = []
        self._connections[game_id].append(websocket)
        logger.info(f"[WS] 新连接: game={game_id}, 当前连接数={len(self._connections[game_id])}")

    def disconnect(self, websocket: WebSocket, game_id: str):
        """移除断开的 WebSocket 连接"""
        if game_id in self._connections:
            self._connections[game_id] = [
                ws for ws in self._connections[game_id] if ws != websocket
            ]
            # 清理空列表
            if not self._connections[game_id]:
                del self._connections[game_id]
            logger.info(f"[WS] 断开连接: game={game_id}")

    async def broadcast(self, game_id: str, message: dict[str, Any]):
        """向指定对局的所有连接广播消息

        参数:
            game_id: 对局 ID
            message: 消息字典，会自动添加 timestamp
        """
        if game_id not in self._connections:
            return

        # 确保消息有时间戳
        if "timestamp" not in message:
            message["timestamp"] = datetime.now().isoformat()

        text = json.dumps(message, ensure_ascii=False)
        dead_connections = []

        for ws in self._connections[game_id]:
            try:
                await ws.send_text(text)
            except Exception as e:
                logger.warning(f"[WS] 发送失败: {e}")
                dead_connections.append(ws)

        # 清理断开的连接
        for ws in dead_connections:
            self.disconnect(ws, game_id)

    def connection_count(self, game_id: str) -> int:
        """获取指定对局的连接数"""
        return len(self._connections.get(game_id, []))


# ─── 全局单例 ─────────────────────────────────────────────
ws_manager = ConnectionManager()
