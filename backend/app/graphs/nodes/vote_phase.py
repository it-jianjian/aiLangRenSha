"""AI 狼人杀 — 投票阶段节点函数

本文件包含投票和淘汰相关的 5 个 LangGraph 节点函数：
1. day_vote_node         — 投票环节：存活玩家投票淘汰一人
2. day_vote_result_node  — 投票结果：计票，判断是否有唯一最高票
3. day_pk_node           — PK 环节：平票时，候选人再次发言+重投
4. day_eliminate_node    — 淘汰+遗言：被淘汰玩家发表遗言，更新状态
5. day_end_node          — 白天结束：平安日检测，轮次准备

调用链：day_speech → day_vote → day_vote_result → [eliminate | pk | peace_day]

PRD 规则要点：
- 投票为明票制（所有人可见谁投了谁）
- 允许弃票（等同于无效票）
- 平票处理：并列最高票 → PK 发言 → 非 PK 玩家重投 → 再平则平安日
- 平安日（无人淘汰）：consecutive_peace_days + 1
- 连续 3 平安日 → 狼人胜（反循环机制）
"""

import asyncio
import logging

from app.config import get_settings
from app.graphs.event_bus import (
    broadcast_persisted_event,
    create_game_event,
    get_alive_players,
    kill_player,
    record_event,
    save_speech,
)
from app.graphs.nodes import timed_node
from app.graphs.nodes.agent_nodes import call_agent_async, call_agent_last_words, call_agent_stream
from app.graphs.state import GameFlowState
from app.models.game import PlayerRole
from app.services.game_rules import can_hunter_shoot
from app.services.human_action_bridge import human_bridge

logger = logging.getLogger(__name__)

HUMAN_LAST_WORDS_TIMEOUT_SECONDS = 60


@timed_node
async def day_vote_node(state: GameFlowState) -> dict:
    """投票环节节点（阶段 1 并行化）

    所有 AI 投票与所有人类 wait_for_action 一起 gather 并行。
    并行后 AI 看不到本轮他人票（预期行为）。
    """
    settings = get_settings()
    alive_players = get_alive_players(state["players"])
    votes: dict[int, int | None] = {}

    tasks = []
    for player in alive_players:
        other_seats = [p["seat_number"] for p in alive_players if p["seat_number"] != player["seat_number"]]
        tasks.append(_do_vote(state, player, other_seats))

    vote_results = await asyncio.gather(*tasks)

    # 合并投票结果
    for voter_seat, target in vote_results:
        votes[voter_seat] = target

    # 并行完成后统一 delay
    if settings.ai_action_delay_vote > 0:
        await asyncio.sleep(settings.ai_action_delay_vote)

    logger.info(f"[Vote] 投票结果: {votes}")
    return {"votes": votes, "is_pk": False, "pk_seats": []}


async def _do_vote(state, player, other_seats) -> tuple[int, int | None]:
    """单玩家投票（可被 gather 并行调用）"""
    if player["player_type"] == "human":
        action = await human_bridge.wait_for_action(
            state["game_id"], "vote",
            {"seat_number": player["seat_number"], "player_name": player["player_name"],
             "role": player.get("role"), "phase": "day",
             "allowed_target_seats": other_seats, "empty_target_actions": ["vote"]},
        )
        target = action.get("target_seat")
    else:
        target = await call_agent_async(state, player, "vote")

    # 校验：不能投自己，不能投已淘汰的
    if target is not None and target not in other_seats:
        target = None  # 非法投票 → 视为弃票

    await record_event(
        state["game_id"], state["current_round"], "day", "vote",
        seat_number=player["seat_number"],
        event_data={"target": target, "is_pk": False},
    )

    return (player["seat_number"], target)


async def _do_pk_vote(state, player, pk_seats) -> tuple[int, int | None]:
    """PK 重投单玩家投票（可被 gather 并行调用）"""
    if player["player_type"] == "human":
        action = await human_bridge.wait_for_action(
            state["game_id"], "vote",
            {"seat_number": player["seat_number"], "player_name": player["player_name"],
             "role": player.get("role"), "phase": "day",
             "allowed_target_seats": pk_seats, "empty_target_actions": ["vote"]},
        )
        target = action.get("target_seat")
    else:
        scoped_state = {**state, "allowed_target_seats": list(pk_seats)}
        target = await call_agent_async(scoped_state, player, "vote")

    if target is not None and target not in pk_seats:
        target = None

    await record_event(
        state["game_id"], state["current_round"], "day", "pk_vote",
        seat_number=player["seat_number"],
        event_data={"target": target},
    )

    return (player["seat_number"], target)


@timed_node
async def day_vote_result_node(state: GameFlowState) -> dict:
    """投票结果节点

    职责：统计投票，判断投票结果走向

    结果分支：
    - 唯一最高票 → day_eliminate（淘汰该玩家）
    - 多人并列最高票 → day_pk（进入 PK 环节）
    - 全部弃票 → day_end（平安日）

    返回：计票结果 + 路由信息
    """
    votes = state["votes"]

    # ─── 计票 ───
    tally: dict[int, int] = {}  # {座位号: 得票数}
    for voter, target in votes.items():
        if target is not None:
            tally[target] = tally.get(target, 0) + 1

    # ─── 记录投票结果事件 ───
    await record_event(
        state["game_id"], state["current_round"], "day", "vote_result",
        event_data={"tally": tally, "votes": {str(k): v for k, v in votes.items()}},
    )

    if not tally:
        # 全部弃票 → 平安日
        logger.info("[VoteResult] 全部弃票，平安日")
        return {"eliminated_seat": None, "is_pk": False, "pk_seats": []}

    # ─── 找最高票 ───
    max_votes = max(tally.values())
    top_seats = [seat for seat, count in tally.items() if count == max_votes]

    if len(top_seats) == 1:
        # 唯一最高票 → 淘汰
        logger.info(f"[VoteResult] {top_seats[0]}号得{max_votes}票，被淘汰")
        return {"eliminated_seat": top_seats[0], "is_pk": False, "pk_seats": []}
    else:
        # 平票 → 进入 PK
        logger.info(f"[VoteResult] 平票: {top_seats}，进入 PK")
        await record_event(
            state["game_id"], state["current_round"], "day", "pk_announce",
            event_data={"tied_seats": top_seats},
        )
        # 保留首轮投票（day_pk_node 返回重投结果时会覆盖 state["votes"]），
        # 供 night_start 摘要与 AI 跨轮记忆使用。
        return {
            "eliminated_seat": None,
            "is_pk": True,
            "pk_seats": top_seats,
            "pre_pk_votes": dict(votes),
        }


@timed_node
async def day_pk_node(state: GameFlowState) -> dict:
    """PK 环节节点

    职责：平票时，PK 候选人再次发言，然后非 PK 玩家重新投票

    流程：
    1. PK 候选人按座位号顺序再次发言
    2. 非 PK 的存活玩家重新投票（只能投 PK 候选人）
    3. 再次计票：唯一最高票 → 淘汰；再平 → 平安日

    PRD 规则：PK 后再次平票 → 本轮无人淘汰（平安日）
    """
    settings = get_settings()
    pk_seats = state["pk_seats"]
    pk_speeches: list[dict] = []

    # ─── PK 发言（串行，游戏规则） ───
    for seat in sorted(pk_seats):
        player = next(p for p in state["players"] if p["seat_number"] == seat)
        if settings.ai_action_delay_day > 0:
            await asyncio.sleep(settings.ai_action_delay_day)

        if player["player_type"] == "human":
            action = await human_bridge.wait_for_action(
                state["game_id"], "speech",
                {"seat_number": player["seat_number"], "player_name": player["player_name"], "role": player.get("role")},
            )
            content = action.get("content", "（该玩家选择沉默）")
        else:
            from app.graphs.nodes.day_phase import _push_speech_chunk, _push_speech_end
            content = await call_agent_stream(
                state, player, "speech",
                on_chunk=lambda d: asyncio.create_task(_push_speech_chunk(state["game_id"], seat, state["current_round"], d)),
            )
            if not content or not content.strip():
                content = f"{seat}号选择沉默。"
            await _push_speech_end(state["game_id"], seat, state["current_round"])

        await save_speech(state["game_id"], state["current_round"], seat, content, is_pk=True)
        await record_event(
            state["game_id"], state["current_round"], "day", "pk_speech",
            seat_number=seat, event_data={"content": content},
        )
        pk_speeches.append({"seat": seat, "content": content})
        logger.info(f"[PK] {seat}号 PK 发言: {content[:40]}...")

    # ─── PK 重投（并行，非 PK 玩家投票，只能在 PK 候选人中选择） ───
    alive_non_pk = [
        p for p in get_alive_players(state["players"])
        if p["seat_number"] not in pk_seats
    ]

    pk_vote_tasks = []
    for voter in alive_non_pk:
        pk_vote_tasks.append(_do_pk_vote(state, voter, pk_seats))

    pk_vote_results = await asyncio.gather(*pk_vote_tasks)

    pk_votes: dict[int, int | None] = {}
    for voter_seat, target in pk_vote_results:
        pk_votes[voter_seat] = target

    if settings.ai_action_delay_vote > 0:
        await asyncio.sleep(settings.ai_action_delay_vote)

    # ─── PK 计票 ───
    tally: dict[int, int] = {}
    for voter, target in pk_votes.items():
        if target is not None:
            tally[target] = tally.get(target, 0) + 1

    await record_event(
        state["game_id"], state["current_round"], "day", "vote_result",
        event_data={"tally": tally, "is_pk": True},
    )

    if not tally:
        logger.info("[PK] PK 重投全部弃票，平安日")
        return {"eliminated_seat": None, "votes": pk_votes, "pk_speeches": pk_speeches}

    max_votes = max(tally.values())
    top_seats = [seat for seat, count in tally.items() if count == max_votes]

    if len(top_seats) == 1:
        logger.info(f"[PK] PK 结果: {top_seats[0]}号被淘汰")
        return {"eliminated_seat": top_seats[0], "votes": pk_votes, "pk_speeches": pk_speeches}
    else:
        # 再次平票 → 平安日
        logger.info(f"[PK] PK 再次平票: {top_seats}，平安日")
        return {"eliminated_seat": None, "votes": pk_votes, "pk_speeches": pk_speeches}


@timed_node
async def day_eliminate_node(state: GameFlowState) -> dict:
    """淘汰+遗言节点

    职责：
    - 标记被淘汰玩家为死亡
    - 被淘汰玩家发表遗言（白天淘汰始终有遗言）
    - 持久化到数据库

    返回：更新后的 players
    """
    settings = get_settings()
    seat = state["eliminated_seat"]
    if seat is None:
        return {}

    players = state["players"]

    # ─── 标记死亡 ───
    players = kill_player(players, seat, state["current_round"], "day", "voted_out")

    # ─── 持久化到 DB ───
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.game import GamePlayer

    async with async_session_factory() as session:
        eliminate_event = create_game_event(
            state["game_id"], state["current_round"], "day", "eliminate",
            seat_number=seat,
            event_data={"is_pk": state.get("is_pk", False)},
        )
        try:
            result = await session.execute(
                select(GamePlayer).where(
                    GamePlayer.game_id == state["game_id"],
                    GamePlayer.seat_number == seat,
                )
            )
            player_db = result.scalar_one_or_none()
            if player_db:
                player_db.is_alive = False
                player_db.death_round = state["current_round"]
                player_db.death_phase = "day"
                player_db.death_reason = "voted_out"
            session.add(eliminate_event)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    # 只有死亡与事件原子提交成功后才向客户端广播。
    await broadcast_persisted_event(eliminate_event)

    # ─── 遗言（有界等待，超时按沉默继续猎人/胜负/下一轮） ───
    player = next(p for p in state["players"] if p["seat_number"] == seat)
    if settings.ai_action_delay_day > 0:
        await asyncio.sleep(settings.ai_action_delay_day)

    if player["player_type"] == "human":
        action = await human_bridge.wait_for_action(
            state["game_id"], "last_words",
            {"seat_number": player["seat_number"], "player_name": player["player_name"], "role": player.get("role")},
            timeout_seconds=HUMAN_LAST_WORDS_TIMEOUT_SECONDS,
        )
        content = action.get("content", "（遗言超时，视为沉默）")
    else:
        content = call_agent_last_words(state, player)

    await save_speech(state["game_id"], state["current_round"], seat, content, is_pk=False)
    await record_event(
        state["game_id"], state["current_round"], "day", "last_words",
        seat_number=seat,
        event_data={"content": content},
    )

    logger.info(f"[Eliminate] {seat}号({player['player_name']})被淘汰，遗言: {content[:40]}...")

    pending_hunter_shot = None
    if player.get("role") in ("hunter", PlayerRole.HUNTER) and can_hunter_shoot("voted_out"):
        pending_hunter_shot = {"seat_number": seat, "trigger": "voted_out", "phase": "day"}

    return {"players": players, "pending_hunter_shot": pending_hunter_shot, "post_victory_route": "after_day"}


@timed_node
async def day_end_node(state: GameFlowState) -> dict:
    """白天结束节点

    职责：
    - 检测是否为平安日（无人淘汰）
    - 更新 consecutive_peace_days 计数
    - 有人淘汰时重置计数为 0

    这个节点的输出会被 victory_check 使用来判断是否触发反循环机制
    """
    eliminated = state.get("eliminated_seat")
    is_pk = state.get("is_pk", False)

    if eliminated is None:
        # 平安日（无论是否经历 PK，只要最终没人淘汰就算）
        new_count = state["consecutive_peace_days"] + 1
        logger.info(f"[DayEnd] 平安日，连续计数: {new_count}")

        # 更新 GameRound 记录
        from sqlalchemy import select

        from app.db.session import async_session_factory
        from app.models.game import GameRound

        async with async_session_factory() as session:
            result = await session.execute(
                select(GameRound).where(
                    GameRound.game_id == state["game_id"],
                    GameRound.round_number == state["current_round"],
                )
            )
            round_db = result.scalar_one_or_none()
            if round_db:
                round_db.is_peace_day = True
                round_db.is_pk_round = is_pk
            await session.commit()

        return {"consecutive_peace_days": new_count, "post_victory_route": "after_day"}
    else:
        # 有人淘汰 → 重置平安日计数
        logger.info(f"[DayEnd] {eliminated}号被淘汰，重置平安日计数")
        return {"consecutive_peace_days": 0, "post_victory_route": "after_day"}
