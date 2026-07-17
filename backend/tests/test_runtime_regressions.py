"""Runtime regressions reproduced from mixed game 7032df00."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session as SyncSession

from app.api.schemas.game_schemas import SpeechRequest
from app.db.session import Base
from app.models.game import Game, GameEvent, GameMode, GamePlayer, PlayerRole
from app.services.game_service import GameService
from app.services.human_action_bridge import HumanActionBridge


async def _atomic_elimination_database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'atomic-elimination.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[Game.__table__, GamePlayer.__table__, GameEvent.__table__],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Game(id="game-1", mode="mixed", status="playing"))
        session.add(GamePlayer(
            id="player-1", game_id="game-1", seat_number=1, player_type="human",
            role="villager", player_name="真人", is_alive=True,
        ))
        await session.commit()
    return engine, factory


def _human_elimination_state():
    return {
        "game_id": "game-1", "current_round": 1, "eliminated_seat": 1, "is_pk": False,
        "players": [
            {"seat_number": 1, "player_name": "真人", "player_type": "human", "role": "villager", "is_alive": True},
            {"seat_number": 2, "player_name": "AI-猎人", "player_type": "ai", "role": "hunter", "is_alive": True},
        ],
    }


@pytest.mark.asyncio
async def test_last_words_submission_uses_the_waiting_action_contract(monkeypatch):
    """The speech endpoint must not submit `speech` while the graph awaits `last_words`."""
    bridge = HumanActionBridge()
    import app.services.human_action_bridge
    monkeypatch.setattr(app.services.human_action_bridge, "human_bridge", bridge)
    waiting = asyncio.create_task(bridge.wait_for_action(
        "game-1", "last_words", {"seat_number": 1, "role": "villager"},
    ))
    await asyncio.sleep(0)

    await GameService(SimpleNamespace()).submit_speech(
        "game-1", 1, SpeechRequest(content="请好人继续找狼", action_type="last_words"),
    )

    assert await waiting == {"action_type": "last_words", "content": "请好人继续找狼"}


@pytest.mark.asyncio
async def test_human_action_timeout_cleans_context_and_allows_flow_to_continue():
    """A disconnected eliminated human must not suspend the in-memory graph forever."""
    bridge = HumanActionBridge()

    action = await bridge.wait_for_action(
        "game-1", "last_words", {"seat_number": 1, "role": "villager"}, timeout_seconds=0.001,
    )

    assert action == {}
    assert bridge.is_waiting("game-1") is False


@pytest.mark.asyncio
async def test_non_tied_human_elimination_is_announced_before_bounded_last_words(monkeypatch):
    """2:1 vote-out must publish elimination before waiting for optional human last words."""
    from app.graphs.nodes import vote_phase

    recorded = []
    saved = []
    prompt_started = asyncio.Event()
    release_prompt = asyncio.Event()
    player_db = SimpleNamespace()

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: player_db)

        def add(self, _entity):
            pass

        async def commit(self):
            pass

        async def rollback(self):
            pass

    class SessionContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            pass

    class Bridge:
        async def wait_for_action(self, _game_id, action_type, _info, timeout_seconds=None):
            assert action_type == "last_words"
            assert timeout_seconds is not None
            prompt_started.set()
            await release_prompt.wait()
            return {"content": "遗言内容"}

    async def record_event(_game_id, _round, _phase, event_type, **kwargs):
        recorded.append((event_type, kwargs))

    async def broadcast_event(event):
        recorded.append((event.event_type, {"seat_number": event.seat_number}))

    async def save_speech(*args, **kwargs):
        saved.append((args, kwargs))

    import app.db.session
    monkeypatch.setattr(app.db.session, "async_session_factory", lambda: SessionContext())
    monkeypatch.setattr(vote_phase, "record_event", record_event)
    monkeypatch.setattr(vote_phase, "broadcast_persisted_event", broadcast_event)
    monkeypatch.setattr(vote_phase, "save_speech", save_speech)
    monkeypatch.setattr(vote_phase, "human_bridge", Bridge())
    monkeypatch.setattr(vote_phase, "AI_ACTION_DELAY", 0)

    state = {
        "game_id": "game-1", "current_round": 1, "eliminated_seat": 1, "is_pk": False,
        "players": [
            {"seat_number": 1, "player_name": "真人", "player_type": "human", "role": "villager", "is_alive": True},
            {"seat_number": 2, "player_name": "AI-猎人", "player_type": "ai", "role": "hunter", "is_alive": True},
        ],
    }
    task = asyncio.create_task(vote_phase.day_eliminate_node(state))
    await prompt_started.wait()

    assert player_db.is_alive is False
    assert [event[0] for event in recorded] == ["eliminate"]

    release_prompt.set()
    result = await task
    assert result["players"][0]["is_alive"] is False
    assert [event[0] for event in recorded] == ["eliminate", "last_words"]
    assert saved


@pytest.mark.asyncio
async def test_eliminate_event_insert_failure_rolls_back_player_death(tmp_path, monkeypatch):
    """B10: event insertion failure must leave both player and event unchanged."""
    from app.graphs import event_bus
    from app.graphs.nodes import vote_phase
    import app.db.session

    engine, factory = await _atomic_elimination_database(tmp_path)

    def fail_eliminate_insert(_mapper, _connection, target):
        if target.event_type == "eliminate":
            raise RuntimeError("simulated eliminate insert failure")

    event.listen(GameEvent, "before_insert", fail_eliminate_insert)
    monkeypatch.setattr(app.db.session, "async_session_factory", factory)
    monkeypatch.setattr(event_bus, "async_session_factory", factory)
    monkeypatch.setattr(vote_phase, "AI_ACTION_DELAY", 0)
    try:
        with pytest.raises(RuntimeError, match="simulated eliminate insert failure"):
            await vote_phase.day_eliminate_node(_human_elimination_state())

        async with factory() as session:
            player = await session.scalar(select(GamePlayer).where(GamePlayer.id == "player-1"))
            event_count = await session.scalar(select(func.count()).select_from(GameEvent))

        assert player.is_alive is True
        assert player.death_reason is None
        assert event_count == 0
    finally:
        event.remove(GameEvent, "before_insert", fail_eliminate_insert)
        await engine.dispose()


@pytest.mark.asyncio
async def test_eliminate_success_commits_one_player_update_and_one_event_once(tmp_path, monkeypatch):
    """B10: player death and one eliminate event are committed together before broadcast."""
    from app.graphs import event_bus
    from app.graphs.nodes import vote_phase
    import app.api.ws_handler
    import app.db.session

    engine, factory = await _atomic_elimination_database(tmp_path)
    commit_count = 0
    prompt_started = asyncio.Event()
    release_prompt = asyncio.Event()
    broadcasts = []
    player_updates = []

    def count_commit(_session):
        nonlocal commit_count
        commit_count += 1

    def count_player_update(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("UPDATE GAME_PLAYERS"):
            player_updates.append(statement)

    class Bridge:
        async def wait_for_action(self, *_args, **_kwargs):
            prompt_started.set()
            await release_prompt.wait()
            return {"content": "遗言"}

    class Manager:
        async def broadcast(self, game_id, payload):
            async with factory() as verification:
                player = await verification.scalar(select(GamePlayer).where(GamePlayer.id == "player-1"))
                event_count = await verification.scalar(select(func.count()).select_from(GameEvent))
            broadcasts.append((game_id, payload, player.is_alive, event_count))

    async def no_op(*_args, **_kwargs):
        pass

    event.listen(SyncSession, "after_commit", count_commit)
    event.listen(engine.sync_engine, "before_cursor_execute", count_player_update)
    monkeypatch.setattr(app.db.session, "async_session_factory", factory)
    monkeypatch.setattr(event_bus, "async_session_factory", factory)
    monkeypatch.setattr(app.api.ws_handler, "ws_manager", Manager())
    monkeypatch.setattr(vote_phase, "human_bridge", Bridge())
    monkeypatch.setattr(vote_phase, "save_speech", no_op)
    monkeypatch.setattr(vote_phase, "record_event", no_op)
    monkeypatch.setattr(vote_phase, "AI_ACTION_DELAY", 0)
    try:
        task = asyncio.create_task(vote_phase.day_eliminate_node(_human_elimination_state()))
        await prompt_started.wait()

        async with factory() as session:
            player = await session.scalar(select(GamePlayer).where(GamePlayer.id == "player-1"))
            events = (await session.scalars(select(GameEvent))).all()

        assert commit_count == 1
        assert len(player_updates) == 1
        assert player.is_alive is False
        assert player.death_reason == "voted_out"
        assert [item.event_type for item in events] == ["eliminate"]
        assert len(broadcasts) == 1
        assert broadcasts[0][2:] == (False, 1)

        release_prompt.set()
        await task
    finally:
        event.remove(SyncSession, "after_commit", count_commit)
        event.remove(engine.sync_engine, "before_cursor_execute", count_player_update)
        release_prompt.set()
        await engine.dispose()


def test_new_ai_names_and_mock_llm_text_are_utf8_chinese():
    """Newly generated names and AI content must survive a real UTF-8 byte round trip."""
    from app.agent.llm import MockWerewolfLLM
    from langchain_core.messages import HumanMessage

    roster = {PlayerRole.WEREWOLF: 2, PlayerRole.VILLAGER: 2, PlayerRole.SEER: 1, PlayerRole.WITCH: 1}
    specs = GameService._build_player_specs(GameMode.PURE_AI, None, roster)
    llm = MockWerewolfLLM(decision_type="speech")
    text = llm.invoke([HumanMessage(content="你的座位号是 2 号；存活玩家座位号: [1, 2, 3]")]).content

    values = [spec["player_name"] for spec in specs] + [text]
    assert all(value.encode("utf-8").decode("utf-8") == value for value in values)
    assert any("冷静" in value or "玩家" in value or "发言" in value for value in values)
