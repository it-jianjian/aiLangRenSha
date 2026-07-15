"""AI 狼人杀 — 胜负判定节点 + 游戏结束节点

本文件包含 2 个 LangGraph 节点函数 + 2 个路由函数：
1. victory_check_node   — 检查胜负条件，决定游戏是否结束
2. game_over_node       — 游戏结束：持久化结果、生成回放数据
3. route_after_victory  — 路由函数：决定下一个节点是 game_over 还是继续
4. route_after_vote     — 路由函数：决定投票结果走向（淘汰/PK/平安日）
5. route_after_pk       — 路由函数：PK 后的结果走向

调用链：
  night_settle → victory_check → [game_over | day_start]
  day_end → victory_check → [game_over | night_start]

PRD 胜负规则：
- 好人胜：所有狼人已淘汰（存活狼人数 = 0）
- 狼人胜：存活狼人 ≥ 存活好人
- 反循环：连续 3 平安日 → 狼人胜
- 超长对局：超 10 轮 → 按存活判定
- 同时满足双方胜利 → 好人优先（鼓励使用技能）
"""

import logging
from datetime import datetime

from app.models.game import PlayerRole, Winner, GameStatus
from app.graphs.state import GameFlowState
from app.graphs.event_bus import record_event, get_alive_players

logger = logging.getLogger(__name__)


def _count_alive_by_faction(players: list[dict]) -> tuple[int, int]:
    """统计存活狼人数和存活好人数

    返回：(werewolf_count, good_count)
    - werewolf_count: 存活的狼人数量
    - good_count: 存活的好人数量（村民 + 预言家 + 女巫）
    """
    werewolf = 0
    good = 0
    for p in players:
        if not p["is_alive"]:
            continue
        if p["role"] == PlayerRole.WEREWOLF:
            werewolf += 1
        else:
            good += 1
    return werewolf, good


async def victory_check_node(state: GameFlowState) -> dict:
    """胜负判定节点

    职责：检查所有胜负条件，决定游戏是否结束

    检查顺序（按优先级）：
    1. 好人胜：存活狼人 = 0
    2. 狼人胜：存活狼人 >= 存活好人
    3. 反循环：连续 3 平安日 → 狼人胜
    4. 超长对局：超 10 轮 → 按当前存活判定
    5. 同时满足 → 好人优先（PRD E17）

    返回：game_over, winner, end_reason
    """
    werewolf_count, good_count = _count_alive_by_faction(state["players"])
    game_over = False
    winner = None
    end_reason = None

    # ─── 条件 1: 所有狼人已淘汰 → 好人胜 ───
    if werewolf_count == 0:
        game_over = True
        winner = Winner.VILLAGER
        end_reason = "all_werewolf_dead"
        logger.info("[Victory] 所有狼人已淘汰，好人阵营胜利！")

    # ─── 条件 2: 狼人 ≥ 好人 → 狼人胜 ───
    elif werewolf_count >= good_count:
        game_over = True
        winner = Winner.WEREWOLF
        end_reason = "werewolf_dominant"
        logger.info(f"[Victory] 狼人({werewolf_count})≥好人({good_count})，狼人阵营胜利！")

    # ─── 条件 3: 连续 3 平安日 → 狼人胜 ───
    elif state["consecutive_peace_days"] >= 3:
        game_over = True
        winner = Winner.WEREWOLF
        end_reason = "deadlock_3peace"
        logger.info("[Victory] 连续3平安日，狼人阵营胜利！")

    # ─── 条件 4: 超过 10 轮 → 按存活判定 ───
    elif state["current_round"] >= 10:
        game_over = True
        if werewolf_count >= good_count:
            winner = Winner.WEREWOLF
        else:
            winner = Winner.VILLAGER
        end_reason = "max_rounds"
        logger.info(f"[Victory] 超过10轮强制结束，{winner}胜")

    else:
        logger.info(f"[Victory] 游戏继续（狼人{werewolf_count} vs 好人{good_count}）")

    # ─── 记录事件（不广播双方人数，防止信息泄露） ───
    await record_event(
        state["game_id"], state["current_round"], "system", "victory_check",
        event_data={
            "game_over": game_over,
            "winner": winner,
        },
    )

    return {"game_over": game_over, "winner": winner, "end_reason": end_reason}


async def game_over_node(state: GameFlowState) -> dict:
    """游戏结束节点

    职责：
    - 持久化游戏结果到 Game 表
    - 记录 game_over 事件
    - 日志输出最终结果
    """
    # ─── 持久化到数据库 ───
    from app.db.session import async_session_factory
    from app.models.game import Game, GameRound
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(select(Game).where(Game.id == state["game_id"]))
        game = result.scalar_one()
        game.status = GameStatus.FINISHED
        game.winner = state["winner"]
        game.end_reason = state["end_reason"]
        game.total_rounds = state["current_round"]
        game.finished_at = datetime.now()

        # 确保最后一轮回合记录存在（如不存在则创建）
        round_result = await session.execute(
            select(GameRound).where(
                GameRound.game_id == state["game_id"],
                GameRound.round_number == state["current_round"],
            )
        )
        round_db = round_result.scalar_one_or_none()
        if not round_db:
            round_db = GameRound(
                game_id=state["game_id"],
                round_number=state["current_round"],
            )
            session.add(round_db)

        await session.commit()

    # ─── 记录 game_over 事件 ───
    all_roles = {
        str(p["seat_number"]): {
            "role": p["role"],
            "is_alive": p["is_alive"],
            "name": p["player_name"],
        }
        for p in state["players"]
    }
    await record_event(
        state["game_id"], state["current_round"], "system", "game_over",
        event_data={
            "winner": state["winner"],
            "end_reason": state["end_reason"],
            "total_rounds": state["current_round"],
            "all_roles": all_roles,
        },
    )

    logger.info(
        f"[GameOver] 对局 {state['game_id']} 结束！"
        f" 胜方: {state['winner']} 原因: {state['end_reason']}"
        f" 总轮数: {state['current_round']}"
    )

    return {"status": "finished"}


# ================================================================
# 路由函数（条件边）
# ================================================================

def route_after_victory(state: GameFlowState) -> str:
    """胜负判定后的路由

    根据 post_victory_route 决定下一个节点：
    - 游戏结束 → game_over
    - 夜晚后的胜负检查 → day_start（进入白天）
    - 白天后的胜负检查 → night_start（进入下一轮夜晚）
    """
    if state["game_over"]:
        return "game_over"

    # 根据来源决定下一站
    route = state.get("post_victory_route", "after_night")
    if route == "after_day":
        return "night_start"   # 白天结束 → 进入下一轮夜晚
    return "day_start"         # 夜晚结束 → 进入白天


def route_after_vote_result(state: GameFlowState) -> str:
    """投票结果路由

    返回：
    - "eliminate": 唯一最高票 → day_eliminate_node
    - "pk": 平票 → day_pk_node
    - "peace_day": 全部弃票/平安日 → day_end_node
    """
    if state.get("eliminated_seat") is not None:
        return "eliminate"
    if state.get("is_pk", False):
        return "pk"
    return "peace_day"


def route_after_pk(state: GameFlowState) -> str:
    """PK 后路由

    返回：
    - "eliminate": PK 后有唯一最高票 → day_eliminate_node
    - "peace_day": PK 后仍然平票 → day_end_node
    """
    if state.get("eliminated_seat") is not None:
        return "eliminate"
    return "peace_day"
