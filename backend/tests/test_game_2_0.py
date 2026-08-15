"""2.0 阵容和核心角色规则的单元测试。"""

import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent.context_filter import filter_context
from app.db.migrations import upgrade_database
from app.db.session import Base
from app.models.game import GamePlayer, PlayerRole
from app.services.game_rules import (
    OFFICIAL_ROSTERS,
    can_hunter_shoot,
    guard_target_is_valid,
    resolve_night_deaths,
    validate_roster,
)
from app.services.game_service import AI_PERSONAS


def test_official_twelve_player_roster_is_valid():
    """12 人官方阵容应包含四狼、四民和四个唯一核心角色。"""
    errors = validate_roster(12, "official", OFFICIAL_ROSTERS[12])

    assert errors == []
    assert OFFICIAL_ROSTERS[12][PlayerRole.HUNTER] == 1
    assert OFFICIAL_ROSTERS[12][PlayerRole.GUARD] == 1


def test_twelve_player_game_has_a_persona_for_every_ai_seat():
    """12 人纯 AI 对局不能因人格列表不足而创建失败。"""
    assert len(AI_PERSONAS) >= 12
    assert len(set(AI_PERSONAS)) >= 12


def test_roster_validation_returns_all_relevant_errors():
    """非法人数、狼人数和核心角色数量应同时返回可读错误。"""
    roster = {
        "werewolf": 3, "villager": 4, "seer": 2,
        "witch": 1, "hunter": 1, "guard": 1,
    }

    errors = validate_roster(12, "custom", roster)

    assert any("4 名狼人" in error for error in errors)
    assert any("预言家" in error for error in errors)


def test_guard_cannot_protect_same_target_on_consecutive_nights():
    """守卫连续守护同一目标必须被拒绝。"""
    valid, reason = guard_target_is_valid(3, 3, [1, 2, 3])

    assert valid is False
    assert "连续" in reason


def test_guard_or_save_prevents_werewolf_kill_but_poison_remains_fatal():
    """守护/解药抵消狼刀，毒药不可抵消且同目标只死亡一次。"""
    deaths = resolve_night_deaths(
        kill_target=3,
        witch_action="poison",
        witch_target=3,
        guard_target=3,
    )

    assert deaths == {3: "poisoned"}


def test_hunter_can_only_shoot_when_killed_by_werewolf_or_vote():
    """猎人被毒杀不可开枪，狼杀和放逐可开枪。"""
    assert can_hunter_shoot("killed_by_werewolf") is True
    assert can_hunter_shoot("voted_out") is True
    assert can_hunter_shoot("poisoned") is False


def test_guard_context_only_contains_own_previous_guard_target():
    """守卫只能得到自己的上夜守护目标，不能得到他人角色。"""
    state = {
        "current_round": 2,
        "players": [
            {"seat_number": 1, "player_name": "守卫", "player_type": "ai", "role": "guard", "is_alive": True},
            {"seat_number": 2, "player_name": "狼", "player_type": "ai", "role": "werewolf", "is_alive": True},
        ],
        "guard_last_target": 2,
    }

    context = filter_context(state, 1, "guard", "guard")

    assert context["guard_last_target"] == 2
    assert all("role" not in player for player in context["players"])


def test_night_resolution_records_both_effects_with_poison_as_primary_cause():
    """同一玩家同时中狼刀和毒药时须保留双重作用且主死因为毒杀。"""
    deaths = resolve_night_deaths(3, "poison", 3, None)

    assert deaths == {3: "poisoned"}


def test_all_backend_sources_compile_before_starting_a_game():
    """流程模块必须在开局前可被 Python 编译器完整导入。"""
    backend_dir = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "app"],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_hunter_revenge_does_not_implicitly_scan_dead_hunters(monkeypatch):
    """hunter_revenge_node 只消费显式 pending_hunter_shot，不扫描本轮死者推导资格。"""
    import asyncio

    from app.graphs.nodes import night_phase

    recorded_events = []

    async def mock_record_event(*args, **kwargs):
        recorded_events.append((args, kwargs))

    async def mock_update_round(*args, **kwargs):
        pass

    monkeypatch.setattr(night_phase, "record_event", mock_record_event)
    monkeypatch.setattr(night_phase, "_update_game_round_summary", mock_update_round)

    # 猎人本轮被狼杀且已标记死亡，但 pending_hunter_shot 未显式设置
    state = {
        "game_id": "test-game",
        "current_round": 1,
        "players": [
            {"seat_number": 1, "player_name": "猎人", "player_type": "ai", "role": PlayerRole.HUNTER,
             "is_alive": False, "death_round": 1, "death_phase": "night",
             "death_reason": "killed_by_werewolf"},
            {"seat_number": 2, "player_name": "村民", "player_type": "ai", "role": PlayerRole.VILLAGER,
             "is_alive": True},
            {"seat_number": 3, "player_name": "狼人", "player_type": "ai", "role": PlayerRole.WEREWOLF,
             "is_alive": True},
        ],
        "pending_hunter_shot": None,
    }

    result = asyncio.run(night_phase.hunter_revenge_node(state))

    assert result == {"pending_hunter_shot": None}
    assert len(recorded_events) == 0, "不得在 pending_hunter_shot 未设置时记录任何猎人事件"


def test_hunter_revenge_processes_explicit_pending_hunter_shot(monkeypatch):
    """hunter_revenge_node 在 pending_hunter_shot 显式设置时正常处理猎人开枪。"""
    import asyncio

    from app.graphs.nodes import night_phase

    recorded_events = []

    async def mock_record_event(*args, **kwargs):
        recorded_events.append((args, kwargs))

    async def mock_update_round(*args, **kwargs):
        pass

    def mock_call_agent_hunter_shoot(state, hunter, alive, trigger, llm=None):
        return alive[0] if alive else None

    monkeypatch.setattr(night_phase, "record_event", mock_record_event)
    monkeypatch.setattr(night_phase, "_update_game_round_summary", mock_update_round)
    monkeypatch.setattr(night_phase, "call_agent_hunter_shoot", mock_call_agent_hunter_shoot)

    state = {
        "game_id": "test-game",
        "current_round": 1,
        "players": [
            {"seat_number": 1, "player_name": "猎人", "player_type": "ai", "role": PlayerRole.HUNTER,
             "is_alive": False, "death_round": 1, "death_phase": "night",
             "death_reason": "killed_by_werewolf"},
            {"seat_number": 2, "player_name": "村民", "player_type": "ai", "role": PlayerRole.VILLAGER,
             "is_alive": True},
            {"seat_number": 3, "player_name": "狼人", "player_type": "ai", "role": PlayerRole.WEREWOLF,
             "is_alive": True},
        ],
        "pending_hunter_shot": {"seat_number": 1, "trigger": "killed_by_werewolf", "phase": "night"},
    }

    result = asyncio.run(night_phase.hunter_revenge_node(state))

    assert result["pending_hunter_shot"] is None  # 处理后清空
    assert len(recorded_events) > 0  # 记录了开枪事件


def test_legacy_2_0_database_upgrades_before_loading_game_player(tmp_path):
    """已记录 2.0 初始 revision 的旧库须补齐令牌摘要列后才能由 ORM 加载。"""
    database_path = tmp_path / "legacy-2-0.db"
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        # 从 ORM metadata 建表，排除 game_players/agent_logs/agent_steps（由迁移添加新列/表）
        legacy_tables = [
            table for table in Base.metadata.sorted_tables
            if table.name not in ("game_players", "agent_logs", "agent_steps")
        ]
        Base.metadata.create_all(engine, tables=legacy_tables)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE game_players (
                    id VARCHAR(36) PRIMARY KEY,
                    game_id VARCHAR(36) NOT NULL,
                    seat_number INTEGER NOT NULL,
                    player_type VARCHAR(8) NOT NULL,
                    role VARCHAR(16) NOT NULL,
                    player_name VARCHAR(32) NOT NULL,
                    is_alive BOOLEAN NOT NULL,
                    death_round INTEGER,
                    death_phase VARCHAR(8),
                    death_reason VARCHAR(32),
                    ai_persona VARCHAR(64),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO game_players (
                    id, game_id, seat_number, player_type, role, player_name,
                    is_alive, created_at, updated_at
                ) VALUES ('player-1', 'game-1', 1, 'human', 'villager', '玩家', 1,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
            # 旧版 agent_logs（无 prompt_tokens/completion_tokens/model_name）
            connection.exec_driver_sql(
                """
                CREATE TABLE agent_logs (
                    id VARCHAR(36) PRIMARY KEY,
                    game_id VARCHAR(36) NOT NULL,
                    round_number INTEGER NOT NULL,
                    seat_number INTEGER NOT NULL,
                    action_type VARCHAR(32) NOT NULL,
                    context_json TEXT,
                    prompt_text TEXT,
                    llm_raw_output TEXT,
                    parsed_decision TEXT,
                    is_fallback BOOLEAN NOT NULL,
                    latency_ms INTEGER,
                    created_at DATETIME NOT NULL
                )
                """
            )
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO alembic_version (version_num) VALUES ('20260716_01')"
            )

        upgrade_database(f"sqlite:///{database_path}")

        with Session(engine) as session:
            player = session.scalar(select(GamePlayer).where(GamePlayer.id == "player-1"))

        assert player is not None
        assert player.access_token_hash is None
    finally:
        engine.dispose()
