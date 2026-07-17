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


def extract_authentication_token(message_text: str) -> str | None:
    """从首帧提取认证令牌；任何非法内容均不回显或记录。"""
    try:
        message = json.loads(message_text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(message, dict) or message.get("type") != "authenticate":
        return None
    data = message.get("data")
    token = data.get("player_token") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None


async def resolve_player_binding(session, game_id: str, player_token: str) -> dict[str, Any] | None:
    """校验人类玩家凭据并给出绑定决策。

    核心规则（修复开局需刷新 bug）：
      - 令牌有效即允许绑定座位，**无论对局处于 waiting 还是 playing**。
        等待态绑定后，对局启动时 _send_game_started 会向已绑定连接投递身份。
      - identity_sync 仅在对局处于 playing 时投递（避免等待房间提前暴露角色）。
      - 狼人同伴列表仅在 playing 且玩家为狼人时查询。

    参数:
        session: 异步数据库会话
        game_id: 对局 ID
        player_token: 人类玩家一次性凭据

    返回:
        None — 凭据无效（应关闭连接，code=1008）
        dict — {
            "seat_number": int,
            "role": str,
            "identity_sync_allowed": bool,  # 是否应立即投递 identity_sync
            "companions": list[int],         # 狼人同伴（仅 playing 狼人）
        }
    """
    from sqlalchemy import select
    from app.models.game import Game, GamePlayer, PlayerType
    from app.services.game_service import GameService

    game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
    if not game:
        return None

    player = (
        await session.execute(
            select(GamePlayer).where(
                GamePlayer.game_id == game_id,
                GamePlayer.player_type == PlayerType.HUMAN,
                GamePlayer.access_token_hash == GameService._token_hash(player_token),
            )
        )
    ).scalar_one_or_none()
    if not player:
        return None

    is_playing = game.status == "playing"
    companions: list[int] = []
    if is_playing and player.role == "werewolf":
        companions = (
            await session.execute(
                select(GamePlayer.seat_number)
                .where(
                    GamePlayer.game_id == game_id,
                    GamePlayer.role == "werewolf",
                    GamePlayer.seat_number != player.seat_number,
                )
                .order_by(GamePlayer.seat_number)
            )
        ).scalars().all()

    return {
        "seat_number": player.seat_number,
        "role": player.role,
        "identity_sync_allowed": is_playing,
        "companions": companions,
    }


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
        self._player_seats: dict[tuple[str, int], int] = {}

    async def connect(self, websocket: WebSocket, game_id: str):
        """接受匿名 WebSocket；认证成功前不绑定人类座位。"""
        await websocket.accept()
        if game_id not in self._connections:
            self._connections[game_id] = []
        self._connections[game_id].append(websocket)
        logger.info(f"[WS] 新连接: game={game_id}, 当前连接数={len(self._connections[game_id])}")

    def bind_player(self, websocket: WebSocket, game_id: str, player_seat: int) -> None:
        """只在首帧凭据校验成功后绑定座位以开启私密消息。"""
        if websocket in self._connections.get(game_id, []):
            self._player_seats[(game_id, id(websocket))] = player_seat

    def disconnect(self, websocket: WebSocket, game_id: str):
        """移除断开的 WebSocket 连接"""
        if game_id in self._connections:
            self._connections[game_id] = [
                ws for ws in self._connections[game_id] if ws != websocket
            ]
            self._player_seats.pop((game_id, id(websocket)), None)
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

    async def send_to_seat(self, game_id: str, player_seat: int, message: dict[str, Any]):
        """向服务端连接时已绑定座位的客户端投递私密消息。"""
        for websocket in list(self._connections.get(game_id, [])):
            if self._player_seats.get((game_id, id(websocket))) != player_seat:
                continue
            payload = dict(message)
            payload.setdefault("timestamp", datetime.now().isoformat())
            try:
                await websocket.send_text(json.dumps(payload, ensure_ascii=False))
            except Exception:
                self.disconnect(websocket, game_id)

    def connection_count(self, game_id: str) -> int:
        """获取指定对局的连接数"""
        return len(self._connections.get(game_id, []))


# ─── 全局单例 ─────────────────────────────────────────────
ws_manager = ConnectionManager()
