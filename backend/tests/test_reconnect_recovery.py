"""AI 狼人杀 — 断线恢复能力单元测试

覆盖：HumanActionBridge.get_pending_action（WS 重连 / HTTP 轮询兜底时
恢复"轮到你了"操作提示，防止 human_action_prompt 推送丢失导致对局卡死）。
"""

import asyncio

import pytest

from app.services.human_action_bridge import HumanActionBridge


@pytest.fixture
def bridge():
    return HumanActionBridge()


async def _start_wait(bridge: HumanActionBridge, game_id: str = "g-1", action_type: str = "vote",
                      player_info: dict | None = None) -> asyncio.Task:
    """以后台任务形式启动 wait_for_action，并等待其完成上下文注册。"""
    info = player_info or {
        "seat_number": 1,
        "player_name": "玩家",
        "role": "villager",
        "phase": "day",
        "allowed_target_seats": [2, 3, 4],
        "empty_target_actions": ["vote"],
    }
    task = asyncio.create_task(bridge.wait_for_action(game_id, action_type, info))
    await asyncio.sleep(0)  # 让协程执行到上下文注册
    return task


class TestGetPendingAction:
    """get_pending_action 断线恢复查询测试"""

    @pytest.mark.asyncio
    async def test_returns_prompt_payload_for_waiting_seat(self, bridge):
        """等待中的座位应拿到与 human_action_prompt 一致的恢复载荷"""
        task = await _start_wait(bridge)
        try:
            pending = bridge.get_pending_action("g-1", 1)

            assert pending is not None
            assert pending["action_type"] == "vote"
            assert pending["seat"] == 1
            assert pending["player_name"] == "玩家"
            assert pending["role"] == "villager"
            assert pending["allowed_target_seats"] == [2, 3, 4]
            assert pending["can_skip"] is True
            assert pending["extra"] == {}
        finally:
            bridge.cancel_wait("g-1")
            await task

    @pytest.mark.asyncio
    async def test_returns_none_for_other_seat_and_unknown_game(self, bridge):
        """等待操作不属于该座位、或对局无等待操作时返回 None"""
        task = await _start_wait(bridge)
        try:
            assert bridge.get_pending_action("g-1", 2) is None
            assert bridge.get_pending_action("other-game", 1) is None
        finally:
            bridge.cancel_wait("g-1")
            await task

    @pytest.mark.asyncio
    async def test_returns_none_after_action_submitted(self, bridge):
        """提交动作后上下文被清除，恢复查询应返回 None"""
        task = await _start_wait(bridge)
        bridge.submit_action("g-1", 1, {"action_type": "vote", "target_seat": 3})
        await task

        assert bridge.get_pending_action("g-1", 1) is None

    @pytest.mark.asyncio
    async def test_witch_extra_is_carried_for_panel_restore(self, bridge):
        """女巫 save 操作的 extra 应携带击杀目标与药水状态，供断线后面板还原"""
        task = await _start_wait(
            bridge, action_type="save",
            player_info={
                "seat_number": 5,
                "player_name": "女巫",
                "role": "witch",
                "phase": "night",
                "allowed_target_seats": [1, 2, 3],
                "empty_target_actions": ["save", "skip"],
                "extra": {"night_kill_target": 3, "save_available": True, "poison_available": False},
            },
        )
        try:
            pending = bridge.get_pending_action("g-1", 5)

            assert pending is not None
            assert pending["action_type"] == "save"
            assert pending["extra"] == {
                "night_kill_target": 3,
                "save_available": True,
                "poison_available": False,
            }
        finally:
            bridge.cancel_wait("g-1")
            await task
