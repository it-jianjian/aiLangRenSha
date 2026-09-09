"""需求一（狼队协商）验收4 · 异常降级 + 构造性协商矩阵单测。

验收4：协商任一步失败 → 整段退回盲投+仲裁（_do_werewolf_legacy(fallback=True)），
对局照常推进、night_kill 正常产生、日志含 [deliberation_fallback] 标记（FR-4）。

构造性矩阵（FR-1）：
  - 轮1一致            → unanimous_first
  - 轮1分歧→轮2收敛    → converged_after_debate（且轮2注入同伴亮牌 extra_briefing）
  - 轮2仍分歧          → stable_ai_priority（座位号最小者优先）
  - 非法 target        → 经 validate_decision 兜底为首个合法非狼目标 + is_fallback（FR-1③）
"""

import asyncio
import logging
from types import SimpleNamespace

from app.graphs.nodes import night_phase
from app.models.game import PlayerRole

ENABLED = SimpleNamespace(wolf_deliberation_enabled=True, wolf_deliberation_max_rounds=1)
LOGGER_NAME = "app.graphs.nodes.night_phase"


def _two_ai_wolf_state() -> dict:
    players = [
        {"seat_number": 1, "player_name": "狼1", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 2, "player_name": "狼2", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 3, "player_name": "民1", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 4, "player_name": "民2", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 5, "player_name": "预言家", "player_type": "ai", "role": PlayerRole.SEER, "is_alive": True},
        {"seat_number": 6, "player_name": "女巫", "player_type": "ai", "role": PlayerRole.WITCH, "is_alive": True},
    ]
    return {"game_id": "g-fb", "current_round": 1, "players": players, "werewolf_seats": [1, 2]}


def _split_wolves(state: dict):
    alive_wolves = [p for p in state["players"] if p["role"] == PlayerRole.WEREWOLF and p["is_alive"]]
    targets = [p["seat_number"] for p in state["players"] if p["role"] != PlayerRole.WEREWOLF and p["is_alive"]]
    return alive_wolves, targets


def _install(monkeypatch):
    """开启协商 + 事件捕获。返回 recorded 列表。"""
    recorded: list[dict] = []

    async def fake_record_event(game_id, round_number, phase, event_type, seat_number=None, event_data=None):
        recorded.append({"type": event_type, "phase": phase, "round": round_number, "data": event_data})

    monkeypatch.setattr(night_phase, "record_event", fake_record_event)
    monkeypatch.setattr(night_phase, "get_settings", lambda: ENABLED)
    return recorded


def _script_proposal(monkeypatch, responses: dict, briefings: list | None = None):
    """按 (seat, round) 脚本化 call_agent_kill_proposal；briefings 非空时捕获每次 extra_briefing。"""
    async def fake_proposal(gs, wolf, extra_briefing=None, deliberation_round=1):
        if briefings is not None:
            briefings.append((wolf["seat_number"], deliberation_round, extra_briefing))
        return responses[(wolf["seat_number"], deliberation_round)]

    monkeypatch.setattr(night_phase, "call_agent_kill_proposal", fake_proposal)


# ─── 验收4：协商异常 → 退回盲投仲裁 ──────────────────────────


def test_round2_exception_falls_back_to_legacy(monkeypatch, caplog):
    """轮2 表态抛异常 → _do_werewolf 退回 legacy(fallback=True)，对局推进且日志带降级标记。"""
    recorded = _install(monkeypatch)

    async def fake_proposal(gs, wolf, extra_briefing=None, deliberation_round=1):
        if deliberation_round == 2:
            raise RuntimeError("模拟轮2 LLM 超时/解析崩溃")
        # 轮1 两狼分歧，强制进入轮2
        return {"target": 5 if wolf["seat_number"] == 1 else 6, "reason": "首轮", "is_fallback": False}

    async def fake_call_agent_async(gs, player, action_type, llm=None):
        assert action_type == "kill"
        return 5  # legacy 兜底：两狼都刀 5 → unanimous

    monkeypatch.setattr(night_phase, "call_agent_kill_proposal", fake_proposal)
    monkeypatch.setattr(night_phase, "call_agent_async", fake_call_agent_async)

    state = _two_ai_wolf_state()
    alive_wolves, targets = _split_wolves(state)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    # 对局照常推进，night_kill 正常产生，结果形状与 legacy 完全一致（下游零改动）
    assert result == {"night_kill_target": 5, "werewolf_agreement": "unanimous"}
    kill = next(e for e in recorded if e["type"] == "night_kill")
    assert kill["data"] == {"target": 5, "agreement": "unanimous", "choices": {"1": 5, "2": 5}}
    # 日志含降级标记（FR-4/验收4）
    assert "deliberation_fallback" in caplog.text


# ─── 构造性协商矩阵（双 AI，FR-1） ───────────────────────────


def test_round1_unanimous_yields_unanimous_first(monkeypatch):
    """轮1 两狼目标即一致 → unanimous_first，无需进入轮2。"""
    recorded = _install(monkeypatch)
    briefings: list = []
    _script_proposal(monkeypatch, {
        (1, 1): {"target": 5, "reason": "刀预言家", "is_fallback": False},
        (2, 1): {"target": 5, "reason": "同意刀预言家", "is_fallback": False},
    }, briefings)

    state = _two_ai_wolf_state()
    alive_wolves, targets = _split_wolves(state)
    result = asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    assert result == {"night_kill_target": 5, "werewolf_agreement": "unanimous_first"}
    # 只有轮1（stage=1）协商事件，未触发轮2
    stages = [e["data"]["stage"] for e in recorded if e["type"] == "werewolf_negotiation"]
    assert stages == [1]
    # 轮1 从不注入同伴亮牌（companion=None）
    assert all(b is None for (_, rnd, b) in briefings if rnd == 1)


def test_round1_split_then_round2_converge(monkeypatch):
    """轮1 分歧 → 轮2 交换理由后收敛到同一目标 → converged_after_debate，且轮2 注入同伴亮牌。"""
    recorded = _install(monkeypatch)
    briefings: list = []
    _script_proposal(monkeypatch, {
        (1, 1): {"target": 5, "reason": "R1_SEAT1", "is_fallback": False},
        (2, 1): {"target": 6, "reason": "R1_SEAT2", "is_fallback": False},
        (1, 2): {"target": 5, "reason": "R2_SEAT1", "is_fallback": False},
        (2, 2): {"target": 5, "reason": "R2_SEAT2", "is_fallback": False},
    }, briefings)

    state = _two_ai_wolf_state()
    alive_wolves, targets = _split_wolves(state)
    result = asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    assert result == {"night_kill_target": 5, "werewolf_agreement": "converged_after_debate"}
    # 轮1 + 轮2 各一条协商事件
    stages = [e["data"]["stage"] for e in recorded if e["type"] == "werewolf_negotiation"]
    assert stages == [1, 2]
    # 轮2 交叉注入：1号收到2号的亮牌理由，2号收到1号的（FR-1②）
    b1_r2 = next(b for (s, r, b) in briefings if s == 1 and r == 2)
    b2_r2 = next(b for (s, r, b) in briefings if s == 2 and r == 2)
    assert b1_r2 and "2号" in b1_r2 and "R1_SEAT2" in b1_r2
    assert b2_r2 and "1号" in b2_r2 and "R1_SEAT1" in b2_r2


def test_round2_still_split_uses_stable_ai_priority(monkeypatch):
    """轮2 仍分歧 → 座位号最小者优先（stable_ai_priority），与 legacy 仲裁一致。"""
    recorded = _install(monkeypatch)
    _script_proposal(monkeypatch, {
        (1, 1): {"target": 5, "reason": "R1_SEAT1", "is_fallback": False},
        (2, 1): {"target": 6, "reason": "R1_SEAT2", "is_fallback": False},
        (1, 2): {"target": 5, "reason": "坚持刀5", "is_fallback": False},
        (2, 2): {"target": 6, "reason": "坚持刀6", "is_fallback": False},
    })

    state = _two_ai_wolf_state()
    alive_wolves, targets = _split_wolves(state)
    result = asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    # sorted(choices)[0] == 1 号 → 取 1 号狼坚持的目标 5
    assert result == {"night_kill_target": 5, "werewolf_agreement": "stable_ai_priority"}
    kill = next(e for e in recorded if e["type"] == "night_kill")
    assert kill["data"]["choices"] == {"1": 5, "2": 6}


# ─── validate_decision 兜底（FR-1③，直接单测 _ai_proposal） ──


def test_illegal_target_sanitized_by_validate_decision(monkeypatch):
    """AI 返回非法目标（狼同伴）→ validate_decision 拦截 → 兜底为首个合法非狼目标 + is_fallback。"""
    async def fake_proposal(gs, wolf, extra_briefing=None, deliberation_round=1):
        # 1 号狼企图刀 2 号狼同伴（非法）
        return {"target": 2, "reason": "误刀同伴", "is_fallback": False}

    monkeypatch.setattr(night_phase, "call_agent_kill_proposal", fake_proposal)

    state = _two_ai_wolf_state()
    wolf = state["players"][0]  # seat 1
    targets = [3, 4, 5, 6]
    out = asyncio.run(night_phase._ai_proposal(state, wolf, targets, deliberation_round=1))

    assert out["seat"] == 1
    assert out["target"] == 3          # targets[0]，首个合法非狼目标
    assert out["is_fallback"] is True  # 标记为兜底


def test_non_integer_target_sanitized_by_validate_decision(monkeypatch):
    """AI 返回非整数目标（解析失败）→ 同样被 validate_decision 兜底。"""
    async def fake_proposal(gs, wolf, extra_briefing=None, deliberation_round=1):
        return {"target": None, "reason": "解析失败", "is_fallback": False}

    monkeypatch.setattr(night_phase, "call_agent_kill_proposal", fake_proposal)

    state = _two_ai_wolf_state()
    wolf = state["players"][1]  # seat 2
    out = asyncio.run(night_phase._ai_proposal(state, wolf, [3, 4, 5, 6], deliberation_round=1))

    assert out["target"] == 3 and out["is_fallback"] is True


def test_legal_target_passes_through(monkeypatch):
    """合法目标原样透传，is_fallback 保持 False。"""
    async def fake_proposal(gs, wolf, extra_briefing=None, deliberation_round=1):
        return {"target": 5, "reason": "刀预言家", "is_fallback": False}

    monkeypatch.setattr(night_phase, "call_agent_kill_proposal", fake_proposal)

    state = _two_ai_wolf_state()
    wolf = state["players"][0]  # seat 1
    out = asyncio.run(night_phase._ai_proposal(state, wolf, [3, 4, 5, 6], deliberation_round=1))

    assert out == {"seat": 1, "target": 5, "reason": "刀预言家", "is_fallback": False}
