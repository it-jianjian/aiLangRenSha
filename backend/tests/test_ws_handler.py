"""AI 狼人杀 — WebSocket 连接管理器单元测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.ws_handler import ConnectionManager, extract_authentication_token, resolve_player_binding
from app.db.session import Base
from app.models.game import Game, GamePlayer, GameMode, PlayerType
from app.services.game_service import GameService


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

    @pytest.mark.asyncio
    async def test_send_to_seat_only_sends_private_message_to_bound_player(self, manager):
        """私密结果必须只投递到服务端已绑定的对应座位连接。"""
        ws1, ws2 = AsyncMock(), AsyncMock()
        await manager.connect(ws1, "game-001")
        await manager.connect(ws2, "game-001")
        manager.bind_player(ws1, "game-001", player_seat=1)
        manager.bind_player(ws2, "game-001", player_seat=2)

        await manager.send_to_seat("game-001", 2, {"type": "private_seer_result", "data": {"target": 1}})

        ws1.send_text.assert_not_called()
        ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnected_human_receives_identity_sync_without_leaking_to_other_seats(self, manager):
        """重连后的同座位连接可恢复私密身份，其他座位绝不能收到。"""
        original, reconnected, observer = AsyncMock(), AsyncMock(), AsyncMock()
        await manager.connect(original, "game-001")
        await manager.connect(reconnected, "game-001")
        await manager.connect(observer, "game-001")
        manager.bind_player(original, "game-001", player_seat=1)
        manager.bind_player(reconnected, "game-001", player_seat=1)
        manager.bind_player(observer, "game-001", player_seat=2)

        await manager.send_to_seat("game-001", 1, {
            "type": "identity_sync",
            "data": {"seat": 1, "role": "werewolf", "werewolf_companions": [4]},
        })

        original.send_text.assert_called_once()
        reconnected.send_text.assert_called_once()
        observer.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_connection_receives_no_private_message_until_authenticated_binding(self, manager):
        """握手完成但首帧尚未认证的连接只能接收公共事件。"""
        websocket = AsyncMock()
        await manager.connect(websocket, "game-001")

        await manager.send_to_seat("game-001", 1, {"type": "identity_sync", "data": {}})

        websocket.send_text.assert_not_called()

    def test_extract_authentication_token_accepts_only_a_valid_first_frame(self):
        """仅规范的 authenticate 首帧可提供令牌；非法内容不回显凭据。"""
        assert extract_authentication_token('{"type":"authenticate","data":{"player_token":"token-value"}}') == "token-value"
        assert extract_authentication_token('{"type":"authenticate","data":{}}') is None
        assert extract_authentication_token('{"type":"other","data":{"player_token":"token-value"}}') is None
        assert extract_authentication_token('not-json') is None

    def test_connection_count_unknown_game(self, manager):
        """未知 game_id 的连接数应为 0"""
        assert manager.connection_count("unknown") == 0


# ================================================================
# WS 认证绑定决策（resolve_player_binding）
# 修复“开局后需手动刷新”bug：等待态也允许绑定，避免 1008 断连
# ================================================================

async def _build_auth_db(tmp_path, status: str, human_role: str = "villager"):
    """构建含一局游戏+人类玩家的异步 SQLite DB，返回 (engine, factory, player_token, game_id)。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ws-auth.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sc: Base.metadata.create_all(
                sc, tables=[Game.__table__, GamePlayer.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    token = "probe-token-abc"
    async with factory() as session:
        session.add(Game(id="g-ws-1", mode=GameMode.MIXED, status=status, player_count=6))
        await session.commit()
    async with factory() as session:
        session.add(GamePlayer(
            id="p-ws-1", game_id="g-ws-1", seat_number=1,
            player_type=PlayerType.HUMAN, role=human_role,
            player_name="探针", is_alive=True,
            access_token_hash=GameService._token_hash(token),
        ))
        await session.commit()
    return engine, factory, token, "g-ws-1"


@pytest.mark.asyncio
async def test_waiting_game_binds_without_identity_sync(tmp_path):
    """等待态绑定应成功但**不**投递身份（角色不应在等待房间提前暴露）。"""
    engine, factory, token, gid = await _build_auth_db(tmp_path, status="waiting")
    try:
        async with factory() as session:
            binding = await resolve_player_binding(session, gid, token)
        assert binding is not None
        assert binding["seat_number"] == 1
        assert binding["identity_sync_allowed"] is False
        assert binding["companions"] == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_playing_game_binds_with_identity_sync(tmp_path):
    """进行中状态绑定应成功并允许投递身份。"""
    engine, factory, token, gid = await _build_auth_db(tmp_path, status="playing", human_role="villager")
    try:
        async with factory() as session:
            binding = await resolve_player_binding(session, gid, token)
        assert binding is not None
        assert binding["identity_sync_allowed"] is True
        assert binding["role"] == "villager"
        assert binding["companions"] == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_playing_werewolf_gets_companions(tmp_path):
    """进行中状态的狼人玩家应获得同伴列表。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ws-wolf.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[Game.__table__, GamePlayer.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    token = "wolf-token"
    async with factory() as session:
        session.add(Game(id="g-ws-2", mode=GameMode.MIXED, status="playing", player_count=6))
        for seat, role, ptype in [(1, "werewolf", PlayerType.HUMAN), (3, "werewolf", PlayerType.AI), (5, "werewolf", PlayerType.AI)]:
            session.add(GamePlayer(
                id=f"p-ws-{seat}", game_id="g-ws-2", seat_number=seat,
                player_type=ptype, role=role, player_name=f"AI-{seat}", is_alive=True,
                access_token_hash=GameService._token_hash(token) if seat == 1 else None,
            ))
        await session.commit()
    try:
        async with factory() as session:
            binding = await resolve_player_binding(session, "g-ws-2", token)
        assert binding is not None
        assert binding["role"] == "werewolf"
        assert binding["identity_sync_allowed"] is True
        assert binding["companions"] == [3, 5]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_token_returns_none(tmp_path):
    """错误令牌应返回 None（连接将被 1008 关闭）。"""
    engine, factory, _token, gid = await _build_auth_db(tmp_path, status="waiting")
    try:
        async with factory() as session:
            binding = await resolve_player_binding(session, gid, "wrong-token")
        assert binding is None
    finally:
        await engine.dispose()


# ================================================================
# 应用层心跳 + 发送超时（云环境反代空闲断连防护）
# ================================================================

class TestHeartbeat:
    """应用层心跳与发送超时测试"""

    @pytest.mark.asyncio
    async def test_heartbeat_removes_dead_connection(self):
        """心跳 ping 发送失败的连接应立即从连接表中移除"""
        import asyncio as _asyncio
        manager = ConnectionManager(heartbeat_interval=0.05, send_timeout=0.1)
        dead_ws = AsyncMock()
        dead_ws.send_text.side_effect = RuntimeError("connection reset")
        manager._connections["game-001"] = [dead_ws]

        manager.start_heartbeat()
        await _asyncio.sleep(0.15)  # 至少触发一轮心跳
        await manager.stop_heartbeat()

        assert manager.connection_count("game-001") == 0

    @pytest.mark.asyncio
    async def test_heartbeat_keeps_alive_connection(self):
        """心跳 ping 成功时连接保留，且收到的是 ping 载荷"""
        import asyncio as _asyncio
        import json as _json
        manager = ConnectionManager(heartbeat_interval=0.05, send_timeout=0.1)
        ws = AsyncMock()
        manager._connections["game-001"] = [ws]

        manager.start_heartbeat()
        await _asyncio.sleep(0.15)
        await manager.stop_heartbeat()

        assert manager.connection_count("game-001") == 1
        sent = _json.loads(ws.send_text.call_args[0][0])
        assert sent["type"] == "ping"

    @pytest.mark.asyncio
    async def test_broadcast_send_timeout_does_not_block_other_connections(self):
        """慢连接发送超时应被移除，不拖慢其他连接的广播"""
        import asyncio as _asyncio
        manager = ConnectionManager(send_timeout=0.05)

        async def _hanging_send(_text):
            await _asyncio.sleep(5)  # 模拟 TCP 缓冲区满的死连接

        slow_ws = AsyncMock()
        slow_ws.send_text.side_effect = _hanging_send
        fast_ws = AsyncMock()
        manager._connections["game-001"] = [slow_ws, fast_ws]

        await _asyncio.wait_for(
            manager.broadcast("game-001", {"type": "test", "data": {}}),
            timeout=2,
        )

        fast_ws.send_text.assert_called_once()
        assert manager.connection_count("game-001") == 1  # 慢连接已被移除
