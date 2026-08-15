"""AI 狼人杀 — LangGraph 游戏主流程编排

职责：将所有节点函数组装成完整的 LangGraph StateGraph，并启动执行

本文件是 P2 阶段的核心产出，实现了完整的狼人杀游戏流程：
  夜晚 → 白天 → 投票 → 胜负检查 → 循环...直到游戏结束

调用链：GameService.start_game() → run_game(game_id) → StateGraph.ainvoke()

图结构：
  START → night_start → night_werewolf → night_seer → night_witch → night_settle
  → victory_check → [game_over | day_start]
  → day_start → day_last_words → day_speech → day_vote → day_vote_result
  → [day_eliminate → day_end → victory_check]
  | [day_pk → (day_eliminate | day_end) → victory_check]
  → victory_check → [game_over | night_start(下一轮)]
  → game_over → END
"""

import logging
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from app.db.session import async_session_factory
from app.graphs.event_bus import record_event
from app.graphs.nodes.day_phase import (
    day_last_words_node,
    day_speech_node,
    day_start_node,
)

# ─── 导入所有节点函数 ─────────────────────────────────────
from app.graphs.nodes.night_phase import (
    hunter_revenge_node,
    night_parallel_actions_node,
    night_settle_node,
    night_start_node,
    night_witch_node,
)
from app.graphs.nodes.victory_check import (
    game_over_node,
    route_after_pk,
    route_after_victory,
    route_after_vote_result,
    victory_check_node,
)
from app.graphs.nodes.vote_phase import (
    day_eliminate_node,
    day_end_node,
    day_pk_node,
    day_vote_node,
    day_vote_result_node,
)
from app.graphs.state import GameFlowState
from app.models.game import (
    Game,
    GamePlayer,
    PlayerRole,
    PlayerType,
)

logger = logging.getLogger(__name__)


async def _send_game_started(game_id: str, initial_state: dict[str, Any]) -> None:
    """仅向服务端已认证绑定的人类座位投递私密身份信息。"""
    human_seat = initial_state.get("human_seat")
    if human_seat is None:
        return
    human_player = next(
        player for player in initial_state["players"] if player["seat_number"] == human_seat
    )
    companions = (
        [seat for seat in initial_state["werewolf_seats"] if seat != human_seat]
        if human_player["role"] == PlayerRole.WEREWOLF else []
    )
    from app.api.ws_handler import ws_manager
    await ws_manager.send_to_seat(game_id, human_seat, {
        "type": "game_started",
        "data": {
            "seat": human_seat,
            "role": human_player["role"],
            "player_name": human_player["player_name"],
            "werewolf_companions": companions,
        },
    })


# ================================================================
# 构建 LangGraph StateGraph
# ================================================================

def build_game_graph() -> StateGraph:
    """构建游戏主流程图

    将所有节点和边组装成 LangGraph StateGraph。
    返回未编译的 StateGraph 对象（编译在 run_game 中进行）。

    节点列表（共 16 个）：
      夜晚: night_start, night_werewolf, night_seer, night_witch, night_settle
      白天: day_start, day_last_words, day_speech
      投票: day_vote, day_vote_result, day_pk, day_eliminate, day_end
      系统: victory_check, game_over
    """
    graph = StateGraph(GameFlowState)

    # ─── 添加所有节点 ───
    # 夜晚阶段（阶段 1：狼人+预言家+守卫并行）
    graph.add_node("night_start", night_start_node)
    graph.add_node("night_parallel_actions", night_parallel_actions_node)
    graph.add_node("night_witch", night_witch_node)
    graph.add_node("hunter_revenge", hunter_revenge_node)
    graph.add_node("night_settle", night_settle_node)

    # 白天阶段
    graph.add_node("day_start", day_start_node)
    graph.add_node("day_last_words", day_last_words_node)
    graph.add_node("day_speech", day_speech_node)

    # 投票阶段
    graph.add_node("day_vote", day_vote_node)
    graph.add_node("day_vote_result", day_vote_result_node)
    graph.add_node("day_pk", day_pk_node)
    graph.add_node("day_eliminate", day_eliminate_node)
    graph.add_node("day_end", day_end_node)

    # 系统节点
    graph.add_node("victory_check", victory_check_node)
    graph.add_node("game_over", game_over_node)

    # ─── 添加固定边（无条件的线性流转） ───
    # 入口：START → 夜晚开始（第一轮）
    graph.add_edge(START, "night_start")

    # 夜晚流程链：开始 → 并行(狼+预言+守卫) → 女巫 → 结算 → 胜负检查
    graph.add_edge("night_start", "night_parallel_actions")
    graph.add_edge("night_parallel_actions", "night_witch")
    graph.add_edge("night_witch", "night_settle")
    graph.add_edge("night_settle", "hunter_revenge")

    # 白天流程链：开始 → 遗言 → 发言 → 投票 → 投票结果
    graph.add_edge("day_start", "day_last_words")
    graph.add_edge("day_last_words", "day_speech")
    graph.add_edge("day_speech", "day_vote")
    graph.add_edge("day_vote", "day_vote_result")

    # 淘汰 → 白天结束（遗言 + 平安日检测）
    graph.add_edge("day_eliminate", "hunter_revenge")

    # 白天结束 → 胜负检查（决定是结束还是进入下一轮）
    graph.add_edge("day_end", "victory_check")

    # 游戏结束 → END
    graph.add_edge("game_over", END)

    # ─── 添加条件边（根据状态决定下一个节点） ───

    # 胜负检查后：游戏结束 / 白天（夜晚后）/ 下一轮夜晚（白天后）
    graph.add_conditional_edges(
        "victory_check",
        route_after_victory,
        {
            "game_over": "game_over",
            "day_start": "day_start",
            "night_start": "night_start",
        },
    )

    # 投票结果后：淘汰 / PK / 平安日
    graph.add_conditional_edges(
        "day_vote_result",
        route_after_vote_result,
        {
            "eliminate": "day_eliminate",  # 唯一最高票 → 淘汰
            "pk": "day_pk",                # 平票 → PK 环节
            "peace_day": "day_end",        # 全部弃票 → 平安日
        },
    )

    # PK 后：淘汰 / 平安日（PK 后不再二次 PK）
    graph.add_conditional_edges(
        "day_pk",
        route_after_pk,
        {
            "eliminate": "day_eliminate",  # PK 后有唯一最高票
            "peace_day": "day_end",        # PK 后仍平票 → 平安日
        },
    )

    graph.add_conditional_edges(
        "hunter_revenge",
        lambda state: "victory_check" if state.get("post_victory_route") == "after_night" else "day_end",
        {"victory_check": "victory_check", "day_end": "day_end"},
    )

    return graph


# ================================================================
# 游戏启动入口
# ================================================================

async def _load_initial_state(game_id: str) -> dict[str, Any]:
    """从数据库加载对局数据，构建 LangGraph 初始状态

    参数:
        game_id: 对局 ID

    返回:
        符合 GameFlowState 结构的初始状态字典
    """
    async with async_session_factory() as session:
        # 加载对局主记录
        from sqlalchemy import select
        result = await session.execute(
            select(Game).where(Game.id == game_id)
        )
        game = result.scalar_one()

        # 加载所有玩家
        players_result = await session.execute(
            select(GamePlayer)
            .where(GamePlayer.game_id == game_id)
            .order_by(GamePlayer.seat_number)
        )
        db_players = players_result.scalars().all()

    # ─── 构建玩家字典列表 ───
    players = []
    werewolf_seats = []
    seer_seat = None
    witch_seat = None
    hunter_seat = None
    guard_seat = None
    human_seat = None

    for p in db_players:
        player_dict = {
            "seat_number": p.seat_number,
            "player_name": p.player_name,
            "player_type": p.player_type,
            "role": p.role,
            "is_alive": p.is_alive,
            "ai_persona": p.ai_persona,
            "death_round": None,
            "death_phase": None,
            "death_reason": None,
        }
        players.append(player_dict)

        # 缓存角色位置
        if p.role == PlayerRole.WEREWOLF:
            werewolf_seats.append(p.seat_number)
        elif p.role == PlayerRole.SEER:
            seer_seat = p.seat_number
        elif p.role == PlayerRole.WITCH:
            witch_seat = p.seat_number
        elif p.role == PlayerRole.HUNTER:
            hunter_seat = p.seat_number
        elif p.role == PlayerRole.GUARD:
            guard_seat = p.seat_number

        if p.player_type == PlayerType.HUMAN:
            human_seat = p.seat_number

    # ─── 组装初始状态（不创建 GameRound，由 night_start_node 负责） ───
    await record_event(
        game_id, 0, "system", "role_assign",
        event_data={
            "players": [
                {"seat": p["seat_number"], "name": p["player_name"], "type": p["player_type"]}
                for p in players
            ]
        },
    )

    # ─── 组装初始状态 ───
    initial_state: dict[str, Any] = {
        "game_id": game_id,
        "mode": game.mode,
        "human_seat": human_seat,
        "players": players,
        "werewolf_seats": werewolf_seats,
        "seer_seat": seer_seat,
        "witch_seat": witch_seat,
        "hunter_seat": hunter_seat,
        "guard_seat": guard_seat,
        "current_round": 0,       # night_start_node 会将其设为 1
        # 夜晚临时数据（初始为空）
        "night_kill_target": None,
        "night_seer_target": None,
        "night_seer_result": None,
        "night_witch_action": "skip",
        "night_witch_target": None,
        "night_guard_target": None,
        "night_deaths": [],
        # 女巫药水（初始未使用）
        "witch_save_used": False,
        "witch_poison_used": False,
        "guard_last_target": None,
        "pending_hunter_shot": None,
        # 白天临时数据（初始为空）
        "speeches": [],
        "votes": {},
        "eliminated_seat": None,
        "is_pk": False,
        "pk_seats": [],
        "pre_pk_votes": {},
        "pk_speeches": [],
        # 胜负
        "game_over": False,
        "winner": None,
        "end_reason": None,
        "consecutive_peace_days": 0,
        "post_victory_route": "after_night",  # 初始为夜晚后（第一轮）
        # 跨轮历史（AI 推理用，不在夜晚重置）
        "game_history": [],
        "seer_history": [],
        # 人类中断点
        "pending_human_action": None,
    }

    return initial_state


async def run_game(game_id: str):
    """运行游戏主流程（完整 P2 实现）

    工作流程：
    1. 从数据库加载对局数据 → 构建初始状态
    2. 编译 LangGraph StateGraph
    3. 调用 ainvoke() 启动图执行
    4. 图会自动按节点→边→节点的顺序运行，直到到达 END

    特点：
    - 纯 AI 模式下全自动运行（无 interrupt）
    - AI 决策通过 LangChain Agent 推理（Mock LLM 降级随机）
    - 所有事件实时写入数据库（支持回放）
    - 游戏结束后持久化最终结果
    """
    logger.info(f"[GameFlow] 对局 {game_id} 启动")

    try:
        # ─── 1. 加载初始状态 ───
        initial_state = await _load_initial_state(game_id)

        # ─── 1.5 通知前端角色信息（混合模式） ───
        if initial_state.get("human_seat") is not None:
            try:
                await _send_game_started(game_id, initial_state)
            except Exception as e:
                logger.debug(f"[GameFlow] WS 定向发送角色失败: {e}")

        # ─── 2. 构建并编译图 ───
        graph = build_game_graph()

        # R1: 使用 AsyncSqliteSaver 持久化检查点，服务重启后可续跑
        from app.config import get_settings
        settings = get_settings()
        db_path = settings.data_dir / "werewolf.db"

        async with AsyncSqliteSaver.from_conn_string(str(db_path).replace("\\", "/")) as checkpointer:
            app = graph.compile(checkpointer=checkpointer)

            # ─── 3. 启动执行 ───
            # ainvoke() 会执行整个图直到到达 END 节点
            # 每个 async 节点函数会被自动 await
            config = {
                "configurable": {"thread_id": game_id},
                "recursion_limit": 200,  # 默认 25 步不够，狼人杀一局可能需要 100+ 步
            }
            final_state = await app.ainvoke(initial_state, config)

        logger.info(
            f"[GameFlow] 对局 {game_id} 完成！"
            f" 胜方: {final_state.get('winner')} "
            f"原因: {final_state.get('end_reason')} "
            f"总轮数: {final_state.get('current_round')}"
        )

    except Exception as e:
        logger.error(f"[GameFlow] 对局 {game_id} 执行异常: {e}", exc_info=True)
        # 异常时将对局标记为结束，避免卡死
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select
                result = await session.execute(select(Game).where(Game.id == game_id))
                game = result.scalar_one_or_none()
                if game:
                    game.status = "finished"
                    game.end_reason = "error"
                    from datetime import datetime
                    game.finished_at = datetime.now()
                await session.commit()
        except Exception as db_err:
            logger.error(f"[GameFlow] 异常状态持久化失败: {db_err}")


async def resume_game(game_id: str):
    """R1: 从检查点恢复中断的对局

    服务重启后，扫描 status=playing 的对局，尝试从最近的检查点续跑。
    如果没有检查点（首次运行从未保存过），则从头开始。
    """
    from app.config import get_settings

    logger.info(f"[GameFlow] 尝试恢复对局 {game_id}")
    settings = get_settings()
    db_path = settings.data_dir / "werewolf.db"

    async with AsyncSqliteSaver.from_conn_string(str(db_path).replace("\\", "/")) as checkpointer:
        graph = build_game_graph()
        app = graph.compile(checkpointer=checkpointer)

        config = {
            "configurable": {"thread_id": game_id},
            "recursion_limit": 200,
        }

        # 检查是否有检查点
        saved = await checkpointer.aget(config)
        if saved:
            # 从检查点续跑
            logger.info(f"[GameFlow] 对局 {game_id} 找到检查点，从断点续跑")
            async for event in app.astream(None, config):
                pass
        else:
            # 无检查点，从头开始
            logger.info(f"[GameFlow] 对局 {game_id} 无检查点，从头开始")
            initial_state = await _load_initial_state(game_id)
            await app.ainvoke(initial_state, config)
