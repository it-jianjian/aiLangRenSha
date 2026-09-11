"""AI 狼人杀 — 白天阶段节点函数

本文件包含白天阶段的 3 个 LangGraph 节点函数：
1. day_start_node       — 白天开始：公布夜晚死亡信息
2. day_last_words_node  — 遗言环节：首夜无遗言，后续夜晚死亡者有遗言
3. day_speech_node      — 发言环节：存活玩家按座位号依次公开发言

调用链：night_settle → victory_check → day_start → day_last_words → day_speech → day_vote

PRD 规则要点：
- 首夜死亡无遗言（经典规则），第二夜起有遗言
- 发言按座位号从小到大，跳过已淘汰玩家
- 每人发言一次，不可追加
- AI 发言为占位文本（P3 替换为 LLM 推理生成）
"""

import asyncio
import logging

from app.config import get_settings
from app.db.session import async_session_factory
from app.graphs.event_bus import (
    get_alive_players,
    record_event,
    save_speech,
)
from app.graphs.nodes import timed_node
from app.graphs.nodes.agent_nodes import call_agent_speech_critiqued, call_agent_stream
from app.graphs.state import GameFlowState
from app.services.human_action_bridge import human_bridge

logger = logging.getLogger(__name__)


async def _push_speech_chunk(game_id: str, seat: int, round_num: int, delta: str):
    """推送单个 speech_chunk 事件到 WebSocket"""
    try:
        from app.api.ws_handler import ws_manager
        await ws_manager.broadcast(game_id, {
            "type": "speech_chunk",
            "data": {"seat": seat, "round": round_num, "delta": delta},
        })
    except Exception as e:
        logger.debug(f"[Speech] chunk 推送失败: {e}")


async def _push_speech_end(game_id: str, seat: int, round_num: int):
    """推送 speech_end 事件"""
    try:
        from app.api.ws_handler import ws_manager
        await ws_manager.broadcast(game_id, {
            "type": "speech_end",
            "data": {"seat": seat, "round": round_num},
        })
    except Exception as e:
        logger.debug(f"[Speech] end 推送失败: {e}")


@timed_node
async def day_start_node(state: GameFlowState) -> dict:
    """白天开始节点

    职责：
    - 公布昨晚的死亡信息（或宣布平安夜）
    - 更新玩家死亡状态到 DB（GamePlayer 表）
    - 记录 phase_change 事件

    返回：无状态变更（只是通知和持久化）
    """
    night_deaths = state["night_deaths"]

    # 广播阶段切换（白天）：前端据此把 currentPhase 置 day，
    # 否则 currentPhase 会停留在 night，导致白天仍显示“夜晚”。
    await record_event(
        state["game_id"], state["current_round"], "day", "phase_change",
        event_data={"phase": "day", "round": state["current_round"]},
    )

    # ─── 持久化死亡信息到 GamePlayer 表 ───
    from sqlalchemy import select

    from app.models.game import GamePlayer

    if night_deaths:
        async with async_session_factory() as session:
            result = await session.execute(
                select(GamePlayer).where(
                    GamePlayer.game_id == state["game_id"],
                    GamePlayer.seat_number.in_(night_deaths),
                )
            )
            for player in result.scalars().all():
                player.is_alive = False
                player.death_round = state["current_round"]
                player.death_phase = "night"
                # death_reason 已在 kill_player 中通过 state 记录
            await session.commit()

    # ─── 公布死亡信息事件 ───
    if night_deaths:
        death_info = []
        for seat in night_deaths:
            p = next((pl for pl in state["players"] if pl["seat_number"] == seat), None)
            if p is None:
                logger.warning(f"[Day] night_deaths 含未知座位 {seat!r}，跳过公布")
                continue
            death_info.append({"seat": seat, "name": p["player_name"]})
        await record_event(
            state["game_id"], state["current_round"], "day", "death_announce",
            event_data={"deaths": death_info},
        )
        names = ", ".join(f"{d['seat']}号({d['name']})" for d in death_info)
        logger.info(f"[Day] 昨晚死亡: {names}")
    else:
        await record_event(
            state["game_id"], state["current_round"], "day", "death_announce",
            event_data={"peace_night": True},
        )
        logger.info("[Day] 昨晚是平安夜，无人死亡")

    await asyncio.sleep(0.3)
    return {}


@timed_node
async def day_last_words_node(state: GameFlowState) -> dict:
    """遗言环节节点

    职责：让夜晚死亡的玩家发表遗言

    PRD 核心规则：
    - 第一夜死亡的玩家 **没有遗言**（经典狼人杀规则）
    - 第二夜及之后死亡的夜晚玩家有一次遗言机会
    - 白天被投票淘汰的玩家始终有遗言（在 day_eliminate_node 处理）

    判断方法：state["current_round"] == 1 → 首夜 → 无遗言
    """
    night_deaths = state["night_deaths"]

    # 首夜无遗言（PRD 规则：第一夜死亡的玩家没有遗言）
    # 只判断轮次，不判断是否有死亡——即使有死亡，首夜也不给遗言
    if state["current_round"] == 1:
        if night_deaths:
            logger.info("[LastWords] 首夜死亡，无遗言环节")
        return {}

    # 非首夜：每个夜晚死亡玩家发表遗言
    current_speeches = list(state.get("speeches", []))  # 获取当前已有发言
    for seat in night_deaths:
        player = next(p for p in state["players"] if p["seat_number"] == seat)
        if not player["is_alive"]:  # 确认已死亡
            settings = get_settings()
            if settings.ai_action_delay_day > 0:
                await asyncio.sleep(settings.ai_action_delay_day)

            # 关键修复：把当前已有发言+遗言写入 state，让后续 AI 能看到
            state["speeches"] = current_speeches

            if player["player_type"] == "human":
                # 人类玩家：等待提交遗言
                action = await human_bridge.wait_for_action(
                    state["game_id"], "last_words",
                    {"seat_number": player["seat_number"], "player_name": player["player_name"], "role": player.get("role")},
                )
                content = action.get("content", "（沉默）")
            else:
                # 阶段 2：流式遗言
                content = await call_agent_stream(
                    state, player, "last_words",
                    on_chunk=lambda d: asyncio.create_task(_push_speech_chunk(state["game_id"], seat, state["current_round"], d)),
                )
                if not content or not content.strip():
                    content = f"{seat}号没有留下遗言。"

            await save_speech(state["game_id"], state["current_round"], seat, content)
            await record_event(
                state["game_id"], state["current_round"], "day", "last_words",
                seat_number=seat,
                event_data={"content": content},
            )
            # 阶段 2：推送 speech_end
            await _push_speech_end(state["game_id"], seat, state["current_round"])
            # 把遗言加入 speeches 列表，让后续玩家能看到
            current_speeches.append({"seat": seat, "content": content})
            logger.info(f"[LastWords] {seat}号遗言: {content[:50]}...")

    return {"speeches": current_speeches}


@timed_node
async def day_speech_node(state: GameFlowState) -> dict:
    """发言环节节点

    职责：所有存活玩家按座位号从小到大依次公开发言

    规则：
    - 按座位号 1→2→3→4→5→6 顺序发言，跳过已淘汰玩家
    - 每人发言一次，不可追加
    - AI 发言为占位文本（P3 替换为 LangChain Agent 推理生成）
    - 人类玩家超时或点"跳过"则显示"该玩家选择沉默"

    返回：speeches 列表（所有发言记录）
    """
    speeches = []
    alive_players = get_alive_players(state["players"])
    # 按座位号排序
    alive_players.sort(key=lambda p: p["seat_number"])

    settings = get_settings()
    for player in alive_players:
        if settings.ai_action_delay_day > 0:
            await asyncio.sleep(settings.ai_action_delay_day)

        # 关键修复：把当前轮已积累的发言写入 state，让后续 AI 能看到本轮发言
        state["speeches"] = speeches

        if player["player_type"] == "human":
            # 人类玩家：等待提交发言
            action = await human_bridge.wait_for_action(
                state["game_id"], "speech",
                {"seat_number": player["seat_number"], "player_name": player["player_name"], "role": player.get("role")},
            )
            content = action.get("content", "（该玩家选择沉默）")
        else:
            # 阶段 2：流式输出（打字机效果）
            def push_chunk(d: str):
                asyncio.create_task(
                    _push_speech_chunk(state["game_id"], player["seat_number"], state["current_round"], d)
                )

            # 需求二：发言批评-修订（方案 A 静默修订），默认关闭；关闭时走原 call_agent_stream（字节级一致）
            critique_scope = [s.strip() for s in settings.speech_critique_scope.split(",")]
            if settings.speech_critique_enabled and "speech" in critique_scope:
                content = await call_agent_speech_critiqued(state, player, "speech", on_chunk=push_chunk)
            else:
                content = await call_agent_stream(state, player, "speech", on_chunk=push_chunk)
            if not content or not content.strip():
                content = f"{player['seat_number']}号选择沉默。"

        speech = {"seat": player["seat_number"], "content": content}
        speeches.append(speech)

        # 持久化到 ChatMessage 表
        await save_speech(state["game_id"], state["current_round"], player["seat_number"], content)

        # 记录事件（用于回放）
        await record_event(
            state["game_id"], state["current_round"], "day", "speech",
            seat_number=player["seat_number"],
            event_data={"content": content},
        )

        # 阶段 2：推送 speech_end
        if player["player_type"] != "human":
            await _push_speech_end(state["game_id"], player["seat_number"], state["current_round"])

        logger.info(f"[Speech] {player['seat_number']}号({player['player_name']}): {content[:40]}...")

    return {"speeches": speeches}
