"""Reviewer B2/B3/B7 regressions: test before implementation."""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.game import GameMode, PlayerRole, PlayerType
from app.services.game_service import GameService
from app.services.human_action_bridge import HumanActionBridge


def test_human_action_verification_requires_server_issued_player_token():
    """REST 行动鉴权不能接受可伪造的 X-Player-Seat 作为身份凭据。"""
    from app.api.game_router import _verify_human_player

    assert "player_token" in inspect.signature(_verify_human_player).parameters


def test_roster_player_specs_rebuild_all_seats_from_latest_snapshot():
    """6→12 或角色替换后，座位必须完全来自最终阵容快照。"""
    roster = {
        PlayerRole.WEREWOLF: 4, PlayerRole.VILLAGER: 4,
        PlayerRole.SEER: 1, PlayerRole.WITCH: 1,
        PlayerRole.HUNTER: 1, PlayerRole.GUARD: 1,
    }

    players = GameService._build_player_specs(GameMode.MIXED, "真人", roster)

    assert len(players) == 12
    assert [player["seat_number"] for player in players] == list(range(1, 13))
    assert sum(player["role"] == PlayerRole.HUNTER for player in players) == 1
    assert sum(player["role"] == PlayerRole.GUARD for player in players) == 1
    assert players[0]["player_type"] == PlayerType.HUMAN


def test_roster_player_specs_keep_existing_human_token_hash_when_rebuilt():
    """重建混合局座位不能使已交付给客户端的人类令牌失效。"""
    roster = {
        PlayerRole.WEREWOLF: 2, PlayerRole.VILLAGER: 2,
        PlayerRole.SEER: 1, PlayerRole.WITCH: 1,
        PlayerRole.HUNTER: 0, PlayerRole.GUARD: 0,
    }

    players = GameService._build_player_specs(
        GameMode.MIXED, "真人", roster, human_access_token_hash="persisted-hash",
    )

    assert players[0]["access_token_hash"] == "persisted-hash"


@pytest.mark.asyncio
async def test_human_prompt_is_sent_only_to_authenticated_player_seat(monkeypatch):
    """私密行动提示不能广播给同局其他 WebSocket 连接。"""
    bridge = HumanActionBridge()
    sent = []

    class Manager:
        async def send_to_seat(self, game_id, seat, payload):
            sent.append((game_id, seat, payload))

    import app.api.ws_handler
    monkeypatch.setattr(app.api.ws_handler, "ws_manager", Manager())

    waiting = asyncio.create_task(
        bridge.wait_for_action("game-1", "guard", {
            "seat_number": 4, "role": "guard", "allowed_target_seats": [2],
        })
    )
    await asyncio.sleep(0)
    bridge.submit_action("game-1", 4, {"action_type": "guard", "target_seat": 2})
    await waiting

    assert len(sent) == 1
    assert sent[0][0] == "game-1"
    assert sent[0][1] == 4
    assert sent[0][2]["type"] == "human_action_prompt"
    data = sent[0][2]["data"]
    assert data["action_type"] == "guard"
    assert data["seat"] == 4
    assert data["role"] == "guard"
    assert data.get("allowed_target_seats") == [2]


@pytest.mark.asyncio
async def test_game_started_role_is_sent_only_to_human_seat(monkeypatch):
    """两个连接共局时，角色和狼人同伴信息只能投递给已绑定的真人座位。"""
    from app.graphs.game_flow import _send_game_started

    sent = []

    class Manager:
        async def send_to_seat(self, game_id, seat, payload):
            sent.append((game_id, seat, payload))

        async def broadcast(self, *_):
            raise AssertionError("私密开局消息不得广播")

    import app.api.ws_handler
    monkeypatch.setattr(app.api.ws_handler, "ws_manager", Manager())

    await _send_game_started("game-1", {
        "human_seat": 2,
        "werewolf_seats": [2, 5],
        "players": [{"seat_number": 2, "role": "werewolf", "player_name": "真人"}],
    })

    assert sent == [("game-1", 2, {"type": "game_started", "data": {
        "seat": 2, "role": "werewolf", "player_name": "真人", "werewolf_companions": [5],
    }})]


@pytest.mark.asyncio
async def test_bridge_rejects_action_from_wrong_seat_before_waking_waiter():
    """已认证但不在当前回合行动的真人不能提前提交或唤醒等待。"""
    bridge = HumanActionBridge()
    waiting = asyncio.create_task(bridge.wait_for_action(
        "game-1", "guard", {"seat_number": 4, "role": "guard", "phase": "night", "allowed_target_seats": [1, 2, 3]},
    ))
    await asyncio.sleep(0)

    with pytest.raises(HTTPException, match="当前等待"):
        bridge.submit_action("game-1", 3, {"action_type": "guard", "target_seat": 2})

    assert not waiting.done()
    bridge.cancel_wait("game-1")
    await waiting


@pytest.mark.asyncio
async def test_bridge_rejects_guard_repeat_and_wrong_action_but_accepts_hunter_skip():
    """服务端必须拒绝连续守护和错误动作，同时允许真人猎人明确跳过。"""
    bridge = HumanActionBridge()
    guard_waiting = asyncio.create_task(bridge.wait_for_action(
        "game-1", "guard", {"seat_number": 4, "role": "guard", "phase": "night", "allowed_target_seats": [1, 2], "last_target": 3},
    ))
    await asyncio.sleep(0)

    with pytest.raises(HTTPException, match="动作类型"):
        bridge.submit_action("game-1", 4, {"action_type": "hunter_shoot", "target_seat": 2})
    with pytest.raises(HTTPException, match="合法目标"):
        bridge.submit_action("game-1", 4, {"action_type": "guard", "target_seat": 3})
    bridge.cancel_wait("game-1")
    await guard_waiting

    hunter_waiting = asyncio.create_task(bridge.wait_for_action(
        "game-1", "hunter_shoot", {
            "seat_number": 4, "role": "hunter", "phase": "day", "allowed_target_seats": [1, 2, 3],
            "empty_target_actions": ["hunter_shoot"],
        },
    ))
    await asyncio.sleep(0)
    bridge.submit_action("game-1", 4, {"action_type": "hunter_shoot", "target_seat": None})

    assert await hunter_waiting == {"action_type": "hunter_shoot", "target_seat": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(("action_type", "role"), [
    ("kill", "werewolf"), ("verify", "seer"), ("poison", "witch"),
])
async def test_bridge_rejects_werewolf_seer_and_witch_targets_without_server_allowlist(action_type, role):
    """狼人、预言家和女巫未声明服务端合法目标集合时不能绕过校验。"""
    bridge = HumanActionBridge()
    waiting = asyncio.create_task(bridge.wait_for_action(
        "game-1", action_type, {"seat_number": 4, "role": role, "phase": "night"},
    ))
    await asyncio.sleep(0)

    with pytest.raises(HTTPException, match="合法目标"):
        bridge.submit_action("game-1", 4, {"action_type": action_type, "target_seat": 99})

    bridge.cancel_wait("game-1")
    await waiting


@pytest.mark.asyncio
async def test_bridge_rejects_guard_empty_target_but_allows_explicit_skip_actions():
    """守卫必须选合法目标；投票、女巫 save/skip、猎人允许明确空目标。"""
    bridge = HumanActionBridge()
    guard_waiting = asyncio.create_task(bridge.wait_for_action(
        "game-1", "guard", {"seat_number": 4, "role": "guard", "allowed_target_seats": [1, 2]},
    ))
    await asyncio.sleep(0)

    with pytest.raises(HTTPException, match="目标不能为空"):
        bridge.submit_action("game-1", 4, {"action_type": "guard", "target_seat": None})

    bridge.cancel_wait("game-1")
    await guard_waiting

    for action_type, payload in (("vote", {"action_type": "vote", "target_seat": None}),
                                 ("save", {"action_type": "skip", "target_seat": None}),
                                 ("hunter_shoot", {"action_type": "hunter_shoot", "target_seat": None})):
        empty_target_actions = ["save", "skip"] if action_type == "save" else [action_type]
        waiting = asyncio.create_task(bridge.wait_for_action(
            "game-1", action_type, {
                "seat_number": 4, "role": "role", "allowed_target_seats": [1, 2],
                "empty_target_actions": empty_target_actions,
            },
        ))
        await asyncio.sleep(0)
        bridge.submit_action("game-1", 4, payload)
        assert await waiting == payload


@pytest.mark.asyncio
async def test_start_requires_successful_conditional_waiting_to_playing_update():
    """未抢到 waiting→playing 条件更新时必须冲突，绝不能调度第二条流程。"""
    class Database:
        async def execute(self, statement):
            return SimpleNamespace(rowcount=0)

        async def flush(self):
            pass

    service = GameService(Database())
    game = SimpleNamespace(
        id="game-1", status="waiting", roster_locked_at=None,
        player_count=6, roster_type="official",
        roster_json='{"werewolf": 2, "villager": 2, "seer": 1, "witch": 1, "hunter": 0, "guard": 0}',
        owner_token_hash=GameService._token_hash("owner"),
    )

    with pytest.raises(HTTPException) as exc:
        await service.start_game(game, "owner")

    assert exc.value.status_code == 409
