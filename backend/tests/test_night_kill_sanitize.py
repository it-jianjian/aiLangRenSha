"""防回归：狼人击杀目标非法（LLM 回吐文本/越界/狼同伴）不得导致对局崩溃。

背景：ReAct kill 路径不做 validate_decision，LLM 可能把工具返回的说明文本当作
decision，使 night_kill_target 变成非座位号；旧代码在 day_start_node 用无默认值
next(...) 直接 StopIteration → RuntimeError → 对局第一轮即被兜底结算。
本测试固化三层防护：_sanitize_kill_target 收敛 / night_settle 过滤 / day_start 跳过。
"""

import asyncio

import pytest

from app.graphs.nodes import night_phase, day_phase
from app.models.game import PlayerRole


def _state():
    players = [
        {"seat_number": 1, "player_name": "狼1", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 2, "player_name": "狼2", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 3, "player_name": "民1", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 4, "player_name": "女巫", "player_type": "ai", "role": PlayerRole.WITCH, "is_alive": True},
        {"seat_number": 5, "player_name": "预言家", "player_type": "ai", "role": PlayerRole.SEER, "is_alive": True},
        {"seat_number": 6, "player_name": "民2", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
    ]
    return {"game_id": "g-san", "current_round": 1, "players": players}


BLOB = "当前查询没有发现任何记录……请确认 game_id / db_path 是否正确"


def test_sanitize_converts_text_target_to_legal_seat():
    s = _state()
    assert night_phase._sanitize_kill_target(s, BLOB) == 3  # 文本 → 首个合法非狼座位
    assert night_phase._sanitize_kill_target(s, 5) == 5     # 合法 int 原样保留
    assert night_phase._sanitize_kill_target(s, 1) == 3     # 狼同伴 → 收敛
    assert night_phase._sanitize_kill_target(s, 99) == 3    # 越界 → 收敛
    assert night_phase._sanitize_kill_target(s, None) == 3  # None → 收敛


def test_night_settle_filters_invalid_death_seat(monkeypatch):
    """night_deaths 含非法座位时，night_settle 应过滤掉而非带崩后续节点。"""
    monkeypatch.setattr(night_phase, "resolve_night_deaths", lambda *a, **k: {BLOB: "killed_by_werewolf"})
    captured = []

    async def fake_record_event(*a, **k):
        captured.append(k.get("event_data"))

    monkeypatch.setattr(night_phase, "record_event", fake_record_event)

    s = _state()
    s.update({"night_kill_target": BLOB, "night_witch_action": "skip",
              "night_witch_target": None, "night_guard_target": None})

    res = asyncio.run(night_phase.night_settle_node(s))
    assert res["night_deaths"] == []
    assert all(p["is_alive"] for p in res["players"])


def test_day_start_tolerates_unknown_death_seat(monkeypatch):
    """day_start_node 遇到 players 中不存在的死亡座位应跳过而非 StopIteration。"""
    captured = []

    async def fake_record_event(game_id, rnd, phase, event_type, seat_number=None, event_data=None):
        captured.append({"type": event_type, "data": event_data})

    class _R:
        def scalars(self):
            return self

        def all(self):
            return []

    class _Session:
        async def execute(self, *a, **k):
            return _R()

        async def commit(self):
            pass

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(day_phase, "record_event", fake_record_event)
    monkeypatch.setattr(day_phase, "async_session_factory", _Factory())

    s = _state()
    s["night_deaths"] = [BLOB]  # 非法死亡座位

    asyncio.run(day_phase.day_start_node(s))  # 不应抛异常
    announce = [c for c in captured if c["type"] == "death_announce"]
    assert announce and announce[0]["data"]["deaths"] == []
