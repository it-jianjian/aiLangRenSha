"""AI 狼人杀 — WebSocket 连接管理器单元测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.api.ws_handler import ConnectionManager


@pytest.fixture
def manager():
    """创建干净的 ConnectionManager 实例"""
    return ConnectionManager()


class TestConnectionManager:
    """WebSocket 连接管理器测试"""

    @pytest.mark.asyncio
    async def test_connect_adds_connection(self, manager):
        """connect 应将连接注册到指定 game_id"""
        ws = AsyncMock()
        await manager.connect(ws, "game-001")
        assert manager.connection_count("game-001") == 1

    @pytest.mark.asyncio
    async def test_connect_multiple_clients(self, manager):
        """同一对局应有多个连接"""
        ws1, ws2 = AsyncMock(), AsyncMock()
        await manager.connect(ws1, "game-001")
        await manager.connect(ws2, "game-001")
        assert manager.connection_count("game-001") == 2

    def test_disconnect_removes_connection(self, manager):
        """disconnect 应移除指定连接"""
        ws1, ws2 = MagicMock(), MagicMock()
        manager._connections["game-001"] = [ws1, ws2]
        manager.disconnect(ws1, "game-001")
        assert manager.connection_count("game-001") == 1

    def test_disconnect_cleans_empty_list(self, manager):
        """最后一个连接断开后应清理空列表"""
        ws = MagicMock()
        manager._connections["game-001"] = [ws]
        manager.disconnect(ws, "game-001")
        assert "game-001" not in manager._connections

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self, manager):
        """broadcast 应向所有连接发送消息"""
        ws1, ws2 = AsyncMock(), AsyncMock()
        manager._connections["game-001"] = [ws1, ws2]

        await manager.broadcast("game-001", {"type": "test", "data": {}})

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_adds_timestamp(self, manager):
        """broadcast 应自动添加 timestamp"""
        ws = AsyncMock()
        manager._connections["game-001"] = [ws]

        await manager.broadcast("game-001", {"type": "test", "data": {}})

        import json
        sent = json.loads(ws.send_text.call_args[0][0])
        assert "timestamp" in sent

    @pytest.mark.asyncio
    async def test_broadcast_no_connections_no_error(self, manager):
        """无连接时 broadcast 不应报错"""
        await manager.broadcast("nonexistent-game", {"type": "test"})  # 不抛异常即通过

    def test_connection_count_unknown_game(self, manager):
        """未知 game_id 的连接数应为 0"""
        assert manager.connection_count("unknown") == 0
