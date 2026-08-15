"""平票 PK 跨轮记忆回归测试。

验证 night_start_node 将上一轮的平票/PK 详情（首轮投票、PK 发言、PK 候选人）
写入 game_history，使 AI 在后续轮次能"记得上轮出现过平票及 battle 过程"。
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import Base
from app.models.game import Game, GameRound


async def _night_start_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pk-history.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sc: Base.metadata.create_all(sc, tables=[Game.__table__, GameRound.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Game(id="g-pk", mode="mixed", status="playing", player_count=6))
        await session.commit()
    return engine, factory


@pytest.mark.asyncio
async def test_night_start_records_pk_details_in_history(tmp_path, monkeypatch):
    """night_start 应将上轮平票首轮投票、PK 发言、候选人写入 game_history。"""
    import app.db.session
    from app.graphs.nodes import night_phase

    engine, factory = await _night_start_db(tmp_path)

    async def _noop_record_event(*_a, **_k):
        pass

    import asyncio as _asyncio
    _real_sleep = _asyncio.sleep
    async def _instant_sleep(_seconds):
        await _real_sleep(0)

    monkeypatch.setattr(night_phase, "record_event", _noop_record_event)
    monkeypatch.setattr(app.db.session, "async_session_factory", factory)
    monkeypatch.setattr(_asyncio, "sleep", _instant_sleep)

    # 上一轮（round 1）发生了平票 PK
    state = {
        "game_id": "g-pk",
        "current_round": 1,          # night_start 会 +1 → new_round=2, prev_round=1
        "players": [
            {"seat_number": 1, "player_name": "A", "player_type": "ai", "role": "werewolf", "is_alive": True},
            {"seat_number": 2, "player_name": "B", "player_type": "ai", "role": "villager", "is_alive": False,
             "death_round": 1, "death_phase": "day", "death_reason": "voted_out"},
            {"seat_number": 3, "player_name": "C", "player_type": "ai", "role": "villager", "is_alive": True},
        ],
        "speeches": [{"seat": 1, "content": "我跳预言家"}],
        "votes": {1: 2, 3: 2},        # PK 重投结果
        "night_deaths": [],
        "eliminated_seat": 2,
        "is_pk": True,
        "pk_seats": [2, 3],
        "pre_pk_votes": {1: 3, 2: 1, 3: 2},   # 首轮平票投票
        "pk_speeches": [
            {"seat": 2, "content": "我是好人别投我"},
            {"seat": 3, "content": "2号才是狼"},
        ],
        "game_history": [],
    }

    result = await night_phase.night_start_node(state)

    history = result["game_history"]
    assert len(history) == 1
    round1 = history[0]
    assert round1["round"] == 1
    assert round1["is_pk"] is True
    assert round1["pk_seats"] == [2, 3]
    # 首轮平票投票被保留（key 被 str 化）
    assert round1["pre_pk_votes"] == {"1": 3, "2": 1, "3": 2}
    # PK 发言被保留
    assert len(round1["pk_speeches"]) == 2
    assert round1["pk_speeches"][0] == {"seat": 2, "content": "我是好人别投我"}
    # 最终投票为 PK 重投结果
    assert round1["votes"] == {"1": 2, "3": 2}
    assert round1["eliminated_seat"] == 2

    # 新字段在夜晚重置
    assert result["pre_pk_votes"] == {}
    assert result["pk_speeches"] == []
    assert result["is_pk"] is False
    assert result["pk_seats"] == []
    await engine.dispose()
