"""游戏进程生命周期回归测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.game import GameStatus
from app.services.game_service import GameService


@pytest.mark.asyncio
async def test_startup_terminates_playing_game_without_recoverable_executor():
    """重启后无内存执行器的 playing 对局必须以明确原因结束，不能永久卡住。"""
    game = SimpleNamespace(status=GameStatus.PLAYING, end_reason=None, finished_at=None)
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [game]))
    session = SimpleNamespace(execute=AsyncMock(return_value=result), flush=AsyncMock())

    terminated = await GameService(session).terminate_unrecoverable_playing_games()

    assert terminated == 1
    assert game.status == GameStatus.FINISHED
    assert game.end_reason == "interrupted_by_restart"
    assert game.finished_at is not None
    session.flush.assert_awaited_once()
