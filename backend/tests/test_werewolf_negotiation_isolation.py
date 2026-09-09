"""需求一（狼队协商）验收1 · 隔离红线测试（FR-3，最高优先级）。

红线：任何协商内容（werewolf_negotiation 事件 / 同伴亮牌 reason）一旦出现在
非狼玩家 prompt、只读工具返回值、公共 WS 广播或对外 replay API 中，即 P0 缺陷。
"""

import asyncio
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.context_filter import filter_context
from app.agent.react_tools import query_death_history, query_speech, query_vote_history
from app.api.replay_router import get_replay
from app.db.session import Base
from app.graphs.event_bus import broadcast_persisted_event
from app.graphs.nodes import night_phase
from app.models.game import Game, GameEvent, GamePlayer, PlayerRole
from app.services.public_events import to_public_event

SECRET_REASON = "SECRET_WOLF_REASON_XYZ"


def _negotiation_event(phase: str = "night") -> SimpleNamespace:
    return SimpleNamespace(
        id="evt-neg",
        game_id="g-iso",
        event_type="werewolf_negotiation",
        event_data=json.dumps({"stage": 1, "proposals": {"1": {"target": 5, "reason": SECRET_REASON}}}),
        phase=phase,
        round_number=1,
        seat_number=None,
        created_at=datetime(2026, 9, 6, 12, 0, 0),
    )


def _two_ai_wolf_state() -> dict:
    players = [
        {"seat_number": 1, "player_name": "狼1", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 2, "player_name": "狼2", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 3, "player_name": "民1", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 4, "player_name": "民2", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 5, "player_name": "预言家", "player_type": "ai", "role": PlayerRole.SEER, "is_alive": True},
        {"seat_number": 6, "player_name": "女巫", "player_type": "ai", "role": PlayerRole.WITCH, "is_alive": True},
    ]
    return {"game_id": "g-iso", "current_round": 1, "players": players, "werewolf_seats": [1, 2]}


def _split_wolves(state: dict):
    alive_wolves = [p for p in state["players"] if p["role"] == PlayerRole.WEREWOLF and p["is_alive"]]
    targets = [p["seat_number"] for p in state["players"] if p["role"] != PlayerRole.WEREWOLF and p["is_alive"]]
    return alive_wolves, targets


# ─── 公共协议 / 广播闸门 ───────────────────────────────────


def test_negotiation_event_is_never_public():
    """werewolf_negotiation 无论 night/day 阶段都不进公共协议（to_public_event → None）。"""
    assert to_public_event(_negotiation_event("night")) is None
    # 防御性冗余：即便被错误标记为 day 阶段，也在 _PRIVATE_EVENT_TYPES 中被拦截
    assert to_public_event(_negotiation_event("day")) is None


def test_broadcast_persisted_event_suppresses_negotiation(monkeypatch):
    """公共广播路径绝不投递协商内容；对照 night_phase 事件仍会正常广播。"""
    import app.api.ws_handler as ws_handler

    broadcasts: list[tuple] = []

    class _Manager:
        async def broadcast(self, game_id, message):
            broadcasts.append((game_id, message))

    monkeypatch.setattr(ws_handler, "ws_manager", _Manager())

    asyncio.run(broadcast_persisted_event(_negotiation_event("night")))
    assert broadcasts == [], "协商事件不得进入公共广播"

    # 对照：night_phase 边界事件是公开事件，应被广播（证明 mock 生效、非全静默）
    control = SimpleNamespace(
        id="evt-night-phase", game_id="g-iso", event_type="night_phase", event_data=None,
        phase="night", round_number=1, seat_number=None, created_at=datetime(2026, 9, 6, 12, 0, 0),
    )
    asyncio.run(broadcast_persisted_event(control))
    assert len(broadcasts) == 1 and broadcasts[0][1]["type"] == "night_phase"


# ─── 对外 replay API 闸门 ─────────────────────────────────


@pytest.mark.asyncio
async def test_replay_api_excludes_negotiation(tmp_path):
    """get_replay 响应中无 werewolf_negotiation 步骤，且任何 step 都不含协商 reason 文本。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sc: Base.metadata.create_all(sc, tables=[Game.__table__, GamePlayer.__table__, GameEvent.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Game(id="g-replay", mode="mixed", status="finished", player_count=6))
        session.add(GamePlayer(id="p1", game_id="g-replay", seat_number=1, player_type="ai",
                               role="werewolf", player_name="狼1", is_alive=True))
        session.add(GamePlayer(id="p5", game_id="g-replay", seat_number=5, player_type="ai",
                               role="seer", player_name="预言家", is_alive=False))
        session.add(GameEvent(id="e1", game_id="g-replay", round_number=1, phase="night", event_type="night_kill",
                              event_data=json.dumps({"target": 5, "agreement": "converged_after_debate", "choices": {"1": 5, "2": 5}})))
        session.add(GameEvent(id="e2", game_id="g-replay", round_number=1, phase="night", event_type="werewolf_negotiation",
                              event_data=json.dumps({"stage": 2, "proposals": {"1": {"target": 5, "reason": SECRET_REASON}}})))
        await session.commit()

        resp = await get_replay("g-replay", session)

    steps = resp.data["steps"]
    assert all(s["event_type"] != "werewolf_negotiation" for s in steps), "replay 不得包含协商事件"
    assert all(SECRET_REASON not in json.dumps(s.get("event_data") or {}, ensure_ascii=False) for s in steps)
    # 对照：night_kill 结果本身仍是回放所需（只排除协商，不误伤击杀事件）
    assert any(s["event_type"] == "night_kill" for s in steps)
    await engine.dispose()


# ─── 非狼 prompt 隔离 + WS 定向投递 ───────────────────────


def test_negotiation_reason_never_pollutes_state_or_non_wolf_context(monkeypatch):
    """协商 reason 仅经 per-call extra_briefing 流转：既不落进共享 state，也不进任何非狼 filter_context。"""
    recorded: list[dict] = []

    async def fake_record_event(game_id, round_number, phase, event_type, seat_number=None, event_data=None):
        recorded.append({"type": event_type, "data": event_data})

    async def fake_proposal(gs, wolf, extra_briefing=None, deliberation_round=1):
        # 两狼首轮分歧 → 触发轮2 交换理由；reason 带哨兵串
        target = 5 if (wolf["seat_number"] == 1 or deliberation_round == 2) else 6
        return {"target": target, "reason": f"{SECRET_REASON}-{wolf['seat_number']}", "is_fallback": False}

    monkeypatch.setattr(night_phase, "record_event", fake_record_event)
    monkeypatch.setattr(night_phase, "get_settings",
                        lambda: SimpleNamespace(wolf_deliberation_enabled=True, wolf_deliberation_max_rounds=1))
    monkeypatch.setattr(night_phase, "call_agent_kill_proposal", fake_proposal)

    state = _two_ai_wolf_state()
    alive_wolves, targets = _split_wolves(state)
    asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    # 哨兵 reason 只应出现在 werewolf_negotiation 私有事件里，绝不进共享 state
    assert SECRET_REASON not in json.dumps(state, ensure_ascii=False, default=str)
    assert any(e["type"] == "werewolf_negotiation" for e in recorded)

    # 全部非狼座位的上下文都不含协商 reason（红线核心）
    for seat, role in [(3, "villager"), (4, "villager"), (5, "seer"), (6, "witch")]:
        for action in ("speech", "vote", "kill", "verify"):
            ctx = filter_context(state, seat, role, action)
            assert SECRET_REASON not in json.dumps(ctx, ensure_ascii=False, default=str)


def test_negotiation_ws_only_targets_werewolf_seats(monkeypatch):
    """协商 WS 仅点对点投递给 werewolf_seats；broadcast 从未被协商路径调用。"""
    import app.api.ws_handler as ws_handler

    sent: list[tuple] = []
    broadcasts: list[tuple] = []

    class _Manager:
        async def send_to_seat(self, game_id, seat, message):
            sent.append((game_id, seat, message))

        async def broadcast(self, game_id, message):
            broadcasts.append((game_id, message))

    async def fake_record_event(*args, **kwargs):
        return None

    async def fake_proposal(gs, wolf, extra_briefing=None, deliberation_round=1):
        target = 5 if (wolf["seat_number"] == 1 or deliberation_round == 2) else 6
        return {"target": target, "reason": f"{SECRET_REASON}-{wolf['seat_number']}", "is_fallback": False}

    monkeypatch.setattr(ws_handler, "ws_manager", _Manager())
    monkeypatch.setattr(night_phase, "record_event", fake_record_event)
    monkeypatch.setattr(night_phase, "get_settings",
                        lambda: SimpleNamespace(wolf_deliberation_enabled=True, wolf_deliberation_max_rounds=1))
    monkeypatch.setattr(night_phase, "call_agent_kill_proposal", fake_proposal)

    state = _two_ai_wolf_state()
    alive_wolves, targets = _split_wolves(state)
    asyncio.run(night_phase._do_werewolf(state, alive_wolves, targets))

    negotiation_msgs = [m for (_, _, m) in sent if m.get("type") == "werewolf_negotiation"]
    assert negotiation_msgs, "协商应通过 send_to_seat 定向投递"
    assert {seat for (_, seat, m) in sent if m.get("type") == "werewolf_negotiation"} <= set(state["werewolf_seats"])
    assert broadcasts == [], "协商路径绝不触发公共 broadcast"


# ─── 只读工具隔离 ─────────────────────────────────────────


def test_react_tools_never_return_negotiation_content(tmp_path):
    """即使协商内容就在 DB 里，三个只读工具（不查 game_events）也绝不返回它。"""
    db_file = str(tmp_path / "tools.db")
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE votes (game_id TEXT, round_number INT, voter_seat INT, target_seat INT, is_pk INT);
        CREATE TABLE chat_messages (game_id TEXT, round_number INT, seat_number INT, content TEXT, is_pk INT, is_last_words INT);
        CREATE TABLE game_players (game_id TEXT, seat_number INT, death_round INT, death_phase TEXT, death_reason TEXT, is_alive INT);
        CREATE TABLE game_events (game_id TEXT, event_type TEXT, event_data TEXT);
        """
    )
    conn.execute("INSERT INTO votes VALUES (?,?,?,?,?)", ("g-tools", 1, 3, 5, 0))
    conn.execute("INSERT INTO chat_messages VALUES (?,?,?,?,?,?)", ("g-tools", 1, 3, "我怀疑5号", 0, 0))
    conn.execute("INSERT INTO game_players VALUES (?,?,?,?,?,?)", ("g-tools", 5, 1, "night", "killed_by_werewolf", 0))
    # 协商内容就躺在 game_events 里（工具从不查这张表）
    conn.execute("INSERT INTO game_events VALUES (?,?,?)",
                 ("g-tools", "werewolf_negotiation", json.dumps({"stage": 1, "proposals": {"1": {"target": 5, "reason": SECRET_REASON}}}, ensure_ascii=False)))
    conn.commit()
    conn.close()

    base = {"game_id": "g-tools", "db_path": db_file, "seat_number": 1, "role": "werewolf"}
    vote_out = query_vote_history.invoke(dict(base))
    speech_out = query_speech.invoke(dict(base))
    death_out = query_death_history.invoke(dict(base))

    # 工具确实返回了真实数据（非空跑），但都不含协商哨兵
    assert "未找到" not in vote_out and "暂无" not in death_out
    for out in (vote_out, speech_out, death_out):
        assert SECRET_REASON not in out
