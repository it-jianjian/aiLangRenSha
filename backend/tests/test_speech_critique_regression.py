"""需求二（发言批评-修订）验收3 · 回归：关闭开关时与改造前字节级一致。

enabled=False（或 scope 不含 speech）时，AI 发言走原 call_agent_stream，
speech 事件 content / save_speech / 返回 speeches 与改造前逐一相等，
且绝不进入批评路径（call_agent_speech_critiqued 零调用）。
"""

import asyncio
from types import SimpleNamespace

from app.graphs.nodes import day_phase
from app.models.game import PlayerRole


def _all_ai_state() -> dict:
    players = [
        {"seat_number": 1, "player_name": "狼1", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 2, "player_name": "狼2", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 3, "player_name": "民1", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 4, "player_name": "民2", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 5, "player_name": "预言家", "player_type": "ai", "role": PlayerRole.SEER, "is_alive": True},
        {"seat_number": 6, "player_name": "女巫", "player_type": "ai", "role": PlayerRole.WITCH, "is_alive": True},
    ]
    return {"game_id": "g-reg", "current_round": 1, "players": players, "werewolf_seats": [1, 2], "speeches": []}


def _install(monkeypatch, enabled, scope="speech", forbid_critique=True):
    recorded: list = []
    saved: list = []
    stream_calls: list = []

    async def fake_record_event(game_id, rnd, phase, event_type, seat_number=None, event_data=None):
        recorded.append({"type": event_type, "phase": phase, "round": rnd, "seat": seat_number, "data": event_data})

    async def fake_save_speech(game_id, rnd, seat, content):
        saved.append((seat, content))

    async def fake_stream(gs, player, action_type, on_chunk, llm=None):
        stream_calls.append((player["seat_number"], action_type))
        content = f"{player['seat_number']}号发言内容"
        on_chunk(content)
        return content

    async def forbidden_critiqued(*a, **k):  # pragma: no cover - 关闭时不得进入
        raise AssertionError("enabled=False 时绝不能调用 call_agent_speech_critiqued")

    async def noop_push(*a, **k):
        return None

    monkeypatch.setattr(day_phase, "record_event", fake_record_event)
    monkeypatch.setattr(day_phase, "save_speech", fake_save_speech)
    monkeypatch.setattr(day_phase, "_push_speech_chunk", noop_push)
    monkeypatch.setattr(day_phase, "_push_speech_end", noop_push)
    monkeypatch.setattr(day_phase, "call_agent_stream", fake_stream)
    monkeypatch.setattr(day_phase, "get_settings", lambda: SimpleNamespace(
        ai_action_delay_day=0, speech_critique_enabled=enabled, speech_critique_scope=scope,
    ))
    if forbid_critique:
        monkeypatch.setattr(day_phase, "call_agent_speech_critiqued", forbidden_critiqued)
    return recorded, saved, stream_calls


def test_disabled_uses_stream_and_matches_legacy(monkeypatch):
    recorded, saved, stream_calls = _install(monkeypatch, enabled=False)
    state = _all_ai_state()

    result = asyncio.run(day_phase.day_speech_node(state))

    # 所有 AI 走原 call_agent_stream（action_type=speech）
    assert [s for (s, a) in stream_calls] == [1, 2, 3, 4, 5, 6]
    assert all(a == "speech" for (_, a) in stream_calls)
    # speech 事件内容与改造前逐一相等
    speech_events = [e for e in recorded if e["type"] == "speech"]
    assert [e["data"]["content"] for e in speech_events] == [f"{i}号发言内容" for i in range(1, 7)]
    assert all(e["phase"] == "day" and e["round"] == 1 for e in speech_events)
    assert [e["seat"] for e in speech_events] == [1, 2, 3, 4, 5, 6]
    # save_speech 逐座位落库、返回 speeches 形状不变
    assert [s for (s, c) in saved] == [1, 2, 3, 4, 5, 6]
    assert result["speeches"][0] == {"seat": 1, "content": "1号发言内容"}
    assert len(result["speeches"]) == 6


def test_enabled_but_scope_excludes_speech_uses_stream(monkeypatch):
    """scope 不含 speech（如只配 last_words）→ 仍走原 call_agent_stream（FR-3 遗言二期不接）。"""
    recorded, saved, stream_calls = _install(monkeypatch, enabled=True, scope="last_words")
    state = _all_ai_state()

    asyncio.run(day_phase.day_speech_node(state))

    assert [s for (s, a) in stream_calls] == [1, 2, 3, 4, 5, 6]
    assert len([e for e in recorded if e["type"] == "speech"]) == 6


def test_enabled_routes_to_critiqued_path(monkeypatch):
    """enabled=True + scope 含 speech → AI 发言走 call_agent_speech_critiqued，不走原流式。"""
    recorded, saved, stream_calls = _install(monkeypatch, enabled=True, forbid_critique=False)
    critiqued_calls: list = []

    async def fake_critiqued(gs, player, action_type, on_chunk):
        critiqued_calls.append(player["seat_number"])
        content = f"{player['seat_number']}号终稿"
        on_chunk(content)
        return content

    monkeypatch.setattr(day_phase, "call_agent_speech_critiqued", fake_critiqued)
    state = _all_ai_state()

    result = asyncio.run(day_phase.day_speech_node(state))

    assert critiqued_calls == [1, 2, 3, 4, 5, 6]
    assert stream_calls == [], "开启后不应再走原 call_agent_stream"
    speech_events = [e for e in recorded if e["type"] == "speech"]
    assert [e["data"]["content"] for e in speech_events] == [f"{i}号终稿" for i in range(1, 7)]
    assert result["speeches"][0] == {"seat": 1, "content": "1号终稿"}
