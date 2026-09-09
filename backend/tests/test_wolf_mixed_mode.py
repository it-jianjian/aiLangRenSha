"""需求一（狼队协商）验收3 · 混合模式测试（FR-2：AI + human）。

AI 先表态 → 其 {seat,target,reason} 经 human_action_prompt.extra.teammate_suggestion
私密推给人类狼 → 人类提交的目标永远生效（agreement='human_priority'）。
并覆盖决策 D5：队友刀书经 get_pending_action 在断线重连时可恢复。
"""

import asyncio
from types import SimpleNamespace

from app.graphs.nodes import night_phase
from app.models.game import PlayerRole
from app.services.human_action_bridge import HumanActionBridge

ENABLED = SimpleNamespace(wolf_deliberation_enabled=True, wolf_deliberation_max_rounds=1)


class _FakeWS:
    def __init__(self):
        self.sent: list[tuple] = []
        self.broadcasts: list[tuple] = []

    async def send_to_seat(self, game_id, seat, message):
        self.sent.append((game_id, seat, message))

    async def broadcast(self, game_id, message):
        self.broadcasts.append((game_id, message))


def _mixed_state() -> dict:
    """1 号 AI 狼 + 2 号人类狼。"""
    players = [
        {"seat_number": 1, "player_name": "狼AI", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 2, "player_name": "狼人类", "player_type": "human", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 3, "player_name": "民1", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 4, "player_name": "民2", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 5, "player_name": "预言家", "player_type": "ai", "role": PlayerRole.SEER, "is_alive": True},
        {"seat_number": 6, "player_name": "女巫", "player_type": "ai", "role": PlayerRole.WITCH, "is_alive": True},
    ]
    return {"game_id": "g-mix", "current_round": 1, "players": players, "werewolf_seats": [1, 2]}


def _split_wolves(state: dict):
    alive_wolves = [p for p in state["players"] if p["role"] == PlayerRole.WEREWOLF and p["is_alive"]]
    targets = [p["seat_number"] for p in state["players"] if p["role"] != PlayerRole.WEREWOLF and p["is_alive"]]
    return alive_wolves, targets


def test_mixed_mode_human_decision_wins_and_sees_teammate_suggestion(monkeypatch):
    """AI 先表态并私密推给人类；人类选不同目标时，人类目标永远生效。"""
    import app.api.ws_handler as ws_handler

    recorded: list[dict] = []
    captured: dict = {}

    async def fake_record_event(game_id, round_number, phase, event_type, seat_number=None, event_data=None):
        recorded.append({"type": event_type, "data": event_data})

    async def fake_proposal(gs, wolf, extra_briefing=None, deliberation_round=1):
        # AI 狼首轮建议刀 5（预言家）
        assert deliberation_round == 1, "混合模式 AI 只表态一轮，不要求二次表态"
        return {"target": 5, "reason": "刀预言家", "is_fallback": False}

    class FakeBridge:
        async def wait_for_action(self, game_id, action_type, player_info, timeout_seconds=None):
            captured["action_type"] = action_type
            captured["player_info"] = player_info
            return {"target_seat": 6}  # 人类偏要刀 6，与 AI 建议不同

    fake_ws = _FakeWS()
    monkeypatch.setattr(ws_handler, "ws_manager", fake_ws)
    monkeypatch.setattr(night_phase, "record_event", fake_record_event)
    monkeypatch.setattr(night_phase, "get_settings", lambda: ENABLED)
    monkeypatch.setattr(night_phase, "call_agent_kill_proposal", fake_proposal)
    monkeypatch.setattr(night_phase, "human_bridge", FakeBridge())

    state = _mixed_state()
    alive_wolves, targets = _split_wolves(state)
    result = asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    # 人类即最终决策
    assert result == {"night_kill_target": 6, "werewolf_agreement": "human_priority"}

    # 人类收到的操作提示 extra 携带 AI 队友刀书
    assert captured["action_type"] == "kill"
    assert captured["player_info"]["extra"]["teammate_suggestion"] == {"seat": 1, "target": 5, "reason": "刀预言家"}

    # night_kill 事件以人类目标为准
    kill = next(e for e in recorded if e["type"] == "night_kill")
    assert kill["data"]["target"] == 6 and kill["data"]["agreement"] == "human_priority"

    # AI 的首轮表态被记录为私有协商事件（stage=1）
    neg = [e for e in recorded if e["type"] == "werewolf_negotiation"]
    assert neg and neg[0]["data"]["stage"] == 1
    assert neg[0]["data"]["proposals"]["1"] == {"target": 5, "reason": "刀预言家"}

    # 协商只定向投递给狼队座位，绝不公共广播
    assert fake_ws.broadcasts == []
    assert {seat for (_, seat, m) in fake_ws.sent if m.get("type") == "werewolf_negotiation"} <= {1, 2}


def test_human_wolf_choice_extra_recovers_via_get_pending_action(monkeypatch):
    """决策 D5：队友刀书透传进 extra，断线重连经 get_pending_action 可完整恢复。"""
    bridge = HumanActionBridge()
    monkeypatch.setattr(night_phase, "human_bridge", bridge)

    state = {"game_id": "g-mix2"}
    wolf = {"seat_number": 2, "player_name": "狼人类", "role": "werewolf", "player_type": "human"}
    suggestion = {"seat": 1, "target": 5, "reason": "刀预言家"}

    async def _run():
        task = asyncio.create_task(
            night_phase._human_wolf_choice(state, wolf, [3, 4, 5, 6], extra={"teammate_suggestion": suggestion})
        )
        await asyncio.sleep(0)  # 让 wait_for_action 注册上下文
        pending = bridge.get_pending_action("g-mix2", 2)
        bridge.submit_action("g-mix2", 2, {"action_type": "kill", "target_seat": 6})
        result = await task
        return pending, result

    pending, result = asyncio.run(_run())

    assert pending is not None
    assert pending["extra"]["teammate_suggestion"] == suggestion
    assert result == (2, 6)
