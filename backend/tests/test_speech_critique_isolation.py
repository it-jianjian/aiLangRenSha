"""需求二（发言批评-修订）P0 隔离红线测试。

评审单含真实身份，泄露级别 P0（PRD"明确不做"末条）：评审单绝不进任何对外可见的推送。
红线：评审单哨兵串一旦出现在 speech 事件数据、WS 广播、replay 可见面，即 P0 缺陷；
它只应出现在 AgentLog.critique_result（唯一合法落点）。

隔离根据（技术方案 D4/H）：批评路径**不产生任何 GameEvent**，故 record_event 只被
"speech" 调用；replay 读 GameEvent、公共广播读 to_public_event，二者都无从拿到评审单。
"""

import asyncio
import json
from types import SimpleNamespace

from app.agent import speech_critique
from app.graphs.nodes import agent_nodes, day_phase
from app.models.game import PlayerRole

SECRET = "SECRET_CRITIQUE_XYZ"


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        return _FakeResp(self._content)


def _all_ai_state() -> dict:
    players = [
        {"seat_number": 1, "player_name": "狼1", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 2, "player_name": "狼2", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 3, "player_name": "民1", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 4, "player_name": "民2", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 5, "player_name": "预言家", "player_type": "ai", "role": PlayerRole.SEER, "is_alive": True},
        {"seat_number": 6, "player_name": "女巫", "player_type": "ai", "role": PlayerRole.WITCH, "is_alive": True},
    ]
    return {"game_id": "g-iso", "current_round": 2, "players": players, "werewolf_seats": [1, 2],
            "speeches": [], "game_history": []}


def _review_with_sentinel() -> str:
    return json.dumps({
        "risk_level": "high",
        "issues": [{"type": "identity_leak", "quote": "他不像狼", "why": SECRET}],
        "fix_hint": SECRET,
    }, ensure_ascii=False)


def _install(monkeypatch, persisted, recorded=None):
    def fake_create_llm(seat_number=None, action_type=None, **kw):
        # critique 返回含哨兵的评审单（high → 触发 revise）；revise 返回干净终稿
        return _FakeLLM(_review_with_sentinel() if action_type == "critique" else "干净终稿，无哨兵。")

    async def fake_stream(gs, player, action_type, on_chunk, llm=None):
        return "我觉得5号不像狼。"  # 泄露草稿（哨兵只在评审单里，不在草稿）

    async def fake_record_event(game_id, rnd, phase, event_type, seat_number=None, event_data=None):
        if recorded is not None:
            recorded.append({"type": event_type, "phase": phase, "data": event_data})

    async def fake_save_speech(*a, **k):
        return None

    async def noop_push(*a, **k):
        return None

    monkeypatch.setattr(speech_critique, "create_llm", fake_create_llm)
    monkeypatch.setattr(agent_nodes, "call_agent_stream", fake_stream)
    monkeypatch.setattr(agent_nodes, "_persist_agent_log", lambda **kw: persisted.append(kw))
    monkeypatch.setattr(day_phase, "record_event", fake_record_event)
    monkeypatch.setattr(day_phase, "save_speech", fake_save_speech)
    monkeypatch.setattr(day_phase, "_push_speech_chunk", noop_push)
    monkeypatch.setattr(day_phase, "_push_speech_end", noop_push)
    monkeypatch.setattr(day_phase, "get_settings", lambda: SimpleNamespace(
        ai_action_delay_day=0, speech_critique_enabled=True, speech_critique_scope="speech",
    ))


def test_critiqued_path_pushes_only_clean_final_and_logs_review(monkeypatch):
    """call_agent_speech_critiqued 外推的 chunk 只有干净终稿；评审单哨兵仅落 AgentLog。"""
    pushed: list = []
    persisted: list = []
    _install(monkeypatch, persisted)

    state = _all_ai_state()
    wolf = state["players"][0]
    out = asyncio.run(agent_nodes.call_agent_speech_critiqued(state, wolf, "speech", on_chunk=pushed.append))

    assert out == "干净终稿，无哨兵。"
    assert pushed == ["干净终稿，无哨兵。"]            # 外推 chunk 只有终稿
    assert all(SECRET not in p for p in pushed)        # 哨兵绝不外推
    assert persisted and len(persisted) == 1
    assert SECRET in persisted[0]["critique_result"]   # 哨兵唯一合法落点
    assert persisted[0]["revised"] is True


def test_day_speech_node_never_emits_review_to_events(monkeypatch):
    """端到端：批评路径不产生任何独立事件，speech 事件只含干净终稿，哨兵只进 AgentLog。"""
    recorded: list = []
    persisted: list = []
    _install(monkeypatch, persisted, recorded)

    state = _all_ai_state()
    result = asyncio.run(day_phase.day_speech_node(state))

    # 1) 批评路径绝不产生独立事件：record_event 只被 "speech" 调用（无 critique/review 事件）
    assert recorded and all(e["type"] == "speech" for e in recorded)
    # 2) 评审单哨兵与结构字段绝不出现在任何事件数据里（→ 也就绝不进 replay / 公共广播）
    blob = json.dumps([e["data"] for e in recorded], ensure_ascii=False)
    assert SECRET not in blob
    assert "fix_hint" not in blob and "risk_level" not in blob and "issue_types" not in blob
    # 3) speech 事件 content == 干净终稿（泄露草稿已被修订掉）
    assert all(e["data"]["content"] == "干净终稿，无哨兵。" for e in recorded)
    assert all(e["phase"] == "day" for e in recorded)
    # 4) 哨兵只落进 AgentLog.critique_result（每座位一条）
    assert persisted and all(SECRET in p["critique_result"] for p in persisted)
    # 5) 返回 speeches 也不含哨兵
    assert all(SECRET not in s["content"] for s in result["speeches"])
