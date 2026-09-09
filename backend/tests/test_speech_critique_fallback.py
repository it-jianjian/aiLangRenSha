"""需求二（发言批评-修订）验收2 · 异常降级 + 验收5 · 修订稿降级路径。

critique/revise 任何异常一律回退草稿原文（FR-3/FR-4），发言永远有产出、对局无感；
修订稿超长→截断、空稿→回退草稿。
"""

import asyncio
import json

from app.agent import speech_critique
from app.graphs.nodes import agent_nodes
from app.models.game import PlayerRole


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        if isinstance(self._content, Exception):
            raise self._content
        return _FakeResp(self._content)


def _wolf_state() -> dict:
    players = [
        {"seat_number": 1, "player_name": "狼1", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 2, "player_name": "狼2", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 3, "player_name": "民1", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 4, "player_name": "民2", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 5, "player_name": "预言家", "player_type": "ai", "role": PlayerRole.SEER, "is_alive": True},
        {"seat_number": 6, "player_name": "女巫", "player_type": "ai", "role": PlayerRole.WITCH, "is_alive": True},
    ]
    return {"game_id": "g-fb", "current_round": 2, "players": players, "werewolf_seats": [1, 2],
            "speeches": [], "game_history": []}


def _install_llm(monkeypatch, critique_out, revise_out):
    def fake_create_llm(seat_number=None, action_type=None, **kw):
        return _FakeLLM(critique_out if action_type == "critique" else revise_out)

    monkeypatch.setattr(speech_critique, "create_llm", fake_create_llm)


# ─── 验收2：critique/revise 异常 → 回退草稿 ─────────────────


def test_critique_exception_returns_draft(monkeypatch):
    _install_llm(monkeypatch, RuntimeError("critique 服务崩溃"), "不应被使用")
    state = _wolf_state()
    wolf = state["players"][0]
    draft = "原始草稿。"

    res = asyncio.run(speech_critique.critique_and_revise(state, wolf, "speech", draft))

    assert res == {"final_text": draft, "critique_result": None, "revised": False, "is_fallback": True}


def test_revise_exception_returns_draft_but_keeps_critique_result(monkeypatch):
    review = json.dumps({"risk_level": "high", "issues": [{"type": "identity_leak"}], "fix_hint": "x"}, ensure_ascii=False)
    _install_llm(monkeypatch, review, RuntimeError("revise 大模型超时"))
    state = _wolf_state()
    wolf = state["players"][0]
    draft = "原始草稿。"

    res = asyncio.run(speech_critique.critique_and_revise(state, wolf, "speech", draft))

    assert res["final_text"] == draft          # 回退草稿
    assert res["revised"] is False
    assert res["is_fallback"] is True
    assert json.loads(res["critique_result"])["risk_level"] == "high"  # 评审单摘要仍留存


# ─── 验收5：修订稿超长 / 空稿降级 ───────────────────────────


def test_revise_overlong_truncated_to_1000(monkeypatch):
    review = json.dumps({"risk_level": "medium", "issues": [], "fix_hint": ""})
    _install_llm(monkeypatch, review, "字" * 1500)
    state = _wolf_state()
    wolf = state["players"][0]

    res = asyncio.run(speech_critique.critique_and_revise(state, wolf, "speech", "草稿"))

    assert len(res["final_text"]) == 1000      # 截断保留前 1000 字
    assert res["revised"] is True and res["is_fallback"] is False


def test_revise_empty_falls_back_to_draft(monkeypatch):
    review = json.dumps({"risk_level": "high", "issues": [], "fix_hint": ""})
    _install_llm(monkeypatch, review, "   \n ")  # 空白修订稿
    state = _wolf_state()
    wolf = state["players"][0]
    draft = "原始草稿。"

    res = asyncio.run(speech_critique.critique_and_revise(state, wolf, "speech", draft))

    assert res["final_text"] == draft
    assert res["revised"] is False and res["is_fallback"] is True


# ─── 验收2 端到端：call_agent_speech_critiqued 崩溃时草稿照产 ──


def test_call_agent_speech_critiqued_returns_draft_on_critique_exception(monkeypatch):
    """critique 崩溃 → call_agent_speech_critiqued 仍返回草稿、外推终稿、落 AgentLog（is_fallback）。"""
    pushed: list = []
    persisted: list = []

    async def fake_stream(gs, player, action_type, on_chunk, llm=None):
        return "原始草稿。"

    monkeypatch.setattr(agent_nodes, "call_agent_stream", fake_stream)
    monkeypatch.setattr(agent_nodes, "_persist_agent_log", lambda **kw: persisted.append(kw))
    _install_llm(monkeypatch, RuntimeError("boom"), "不应被使用")

    state = _wolf_state()
    wolf = state["players"][0]
    out = asyncio.run(agent_nodes.call_agent_speech_critiqued(state, wolf, "speech", on_chunk=pushed.append))

    assert out == "原始草稿。"
    assert pushed == ["原始草稿。"]                      # 终稿（=草稿）仍被外推
    assert persisted and len(persisted) == 1
    assert persisted[0]["critique_result"] is None       # critique 崩溃时无评审单
    assert persisted[0]["revised"] is False
    assert persisted[0]["is_fallback"] is True


def test_call_agent_speech_critiqued_empty_draft_skips_critique(monkeypatch):
    """空草稿直接走原沉默兜底，不进审稿（不调用 create_llm）。"""
    pushed: list = []
    persisted: list = []
    called = {"llm": False}

    async def fake_stream(gs, player, action_type, on_chunk, llm=None):
        return "   "  # 空白草稿

    def fake_create_llm(*a, **k):
        called["llm"] = True
        return _FakeLLM("{}")

    monkeypatch.setattr(agent_nodes, "call_agent_stream", fake_stream)
    monkeypatch.setattr(agent_nodes, "_persist_agent_log", lambda **kw: persisted.append(kw))
    monkeypatch.setattr(speech_critique, "create_llm", fake_create_llm)

    state = _wolf_state()
    wolf = state["players"][0]
    out = asyncio.run(agent_nodes.call_agent_speech_critiqued(state, wolf, "speech", on_chunk=pushed.append))

    assert out == "   "
    assert pushed == [] and persisted == [] and called["llm"] is False
