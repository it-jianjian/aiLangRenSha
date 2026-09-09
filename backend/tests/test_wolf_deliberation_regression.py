"""需求一（狼队协商）验收2 · 回归测试：关闭协商时与改造前字节级一致。

同一确定性 AI 输入下，enabled=False 时 _do_werewolf 的 target / agreement /
night_kill 事件序列必须与改造前逐一相等，且绝不产生 werewolf_negotiation 事件、
绝不进入协商路径（call_agent_kill_proposal / _do_werewolf_deliberate 零调用）。
"""

import asyncio
from types import SimpleNamespace

from app.graphs.nodes import night_phase
from app.models.game import PlayerRole

DISABLED = SimpleNamespace(wolf_deliberation_enabled=False, wolf_deliberation_max_rounds=1)


def _two_ai_wolf_state() -> dict:
    players = [
        {"seat_number": 1, "player_name": "狼1", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 2, "player_name": "狼2", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 3, "player_name": "民1", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 4, "player_name": "民2", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 5, "player_name": "预言家", "player_type": "ai", "role": PlayerRole.SEER, "is_alive": True},
        {"seat_number": 6, "player_name": "女巫", "player_type": "ai", "role": PlayerRole.WITCH, "is_alive": True},
    ]
    return {"game_id": "g-reg", "current_round": 1, "players": players, "werewolf_seats": [1, 2]}


def _split_wolves(state: dict):
    alive_wolves = [p for p in state["players"] if p["role"] == PlayerRole.WEREWOLF and p["is_alive"]]
    targets = [p["seat_number"] for p in state["players"] if p["role"] != PlayerRole.WEREWOLF and p["is_alive"]]
    return alive_wolves, targets


def _install(monkeypatch, targets_by_seat: dict):
    """关闭协商 + 确定性 AI 击杀 + 事件捕获 + 协商路径探针（一旦被调用即失败）。"""
    recorded: list[dict] = []

    async def fake_record_event(game_id, round_number, phase, event_type, seat_number=None, event_data=None):
        recorded.append({"game_id": game_id, "round": round_number, "phase": phase,
                         "type": event_type, "seat": seat_number, "data": event_data})

    async def fake_call_agent_async(gs, player, action_type, llm=None):
        assert action_type == "kill"
        return targets_by_seat[player["seat_number"]]

    def _forbidden_deliberate(*args, **kwargs):  # pragma: no cover - 关闭时不得进入
        raise AssertionError("enabled=False 时绝不能进入 _do_werewolf_deliberate")

    async def _forbidden_proposal(*args, **kwargs):  # pragma: no cover - 关闭时不得调用
        raise AssertionError("enabled=False 时绝不能调用 call_agent_kill_proposal")

    monkeypatch.setattr(night_phase, "record_event", fake_record_event)
    monkeypatch.setattr(night_phase, "get_settings", lambda: DISABLED)
    monkeypatch.setattr(night_phase, "call_agent_async", fake_call_agent_async)
    monkeypatch.setattr(night_phase, "_do_werewolf_deliberate", _forbidden_deliberate)
    monkeypatch.setattr(night_phase, "call_agent_kill_proposal", _forbidden_proposal)
    return recorded


def test_disabled_double_ai_unanimous_matches_legacy(monkeypatch):
    """两 AI 狼目标一致 → agreement='unanimous'（原值，非 unanimous_first），事件形状不变。"""
    recorded = _install(monkeypatch, {1: 5, 2: 5})
    state = _two_ai_wolf_state()
    alive_wolves, targets = _split_wolves(state)

    result = asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    assert result == {"night_kill_target": 5, "werewolf_agreement": "unanimous"}
    assert [e["type"] for e in recorded] == ["night_kill"], "关闭时只应产生 night_kill，无协商事件"
    kill = recorded[0]
    assert kill["phase"] == "night" and kill["round"] == 1
    assert kill["data"] == {"target": 5, "agreement": "unanimous", "choices": {"1": 5, "2": 5}}


def test_disabled_double_ai_split_uses_stable_ai_priority(monkeypatch):
    """两 AI 狼目标分歧 → 座位号最小者优先（stable_ai_priority），与改造前一致。"""
    recorded = _install(monkeypatch, {1: 6, 2: 5})
    state = _two_ai_wolf_state()
    alive_wolves, targets = _split_wolves(state)

    result = asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    # sorted(choices)[0] == 1 → 取 1 号狼的目标 6
    assert result == {"night_kill_target": 6, "werewolf_agreement": "stable_ai_priority"}
    assert [e["type"] for e in recorded] == ["night_kill"]
    assert recorded[0]["data"] == {"target": 6, "agreement": "stable_ai_priority", "choices": {"1": 6, "2": 5}}


def test_disabled_never_emits_negotiation_event(monkeypatch):
    """关闭协商时，无论如何都不产生 werewolf_negotiation 事件（隔离红线的关闭侧保证）。"""
    recorded = _install(monkeypatch, {1: 5, 2: 6})
    state = _two_ai_wolf_state()
    alive_wolves, targets = _split_wolves(state)

    asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    assert not any(e["type"] == "werewolf_negotiation" for e in recorded)
