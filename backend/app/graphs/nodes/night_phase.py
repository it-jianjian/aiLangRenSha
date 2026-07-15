"""AI 狼人杀 — 夜晚阶段节点函数

本文件包含夜晚阶段的 5 个 LangGraph 节点函数：
1. night_start_node     — 夜晚开始，重置夜晚临时数据，轮次+1
2. night_werewolf_node  — 狼人行动：选择击杀目标（双狼协商）
3. night_seer_node      — 预言家行动：查验一名玩家身份
4. night_witch_node     — 女巫行动：选择使用解药/毒药/跳过
5. night_settle_node    — 夜晚结算：综合三方行动，确定最终死亡名单

调用链：night_start → night_werewolf → night_seer → night_witch → night_settle → victory_check

PRD 规则要点：
- 狼人必须击杀目标（不能空刀），2 狼不一致时 AI 随机/人类优先
- 预言家可查验任何存活的其他玩家（不能验自己）
- 女巫每晚只能用一种药（解药/毒药互斥），各限用一次（整局）
- 首夜女巫可以自救
"""

import asyncio
import logging
import random

from app.models.game import PlayerRole
from app.graphs.state import GameFlowState
from app.graphs.event_bus import (
    record_event,
    get_alive_players,
    get_alive_by_role,
    kill_player,
)
from app.graphs.nodes.agent_nodes import call_agent_werewolf_kill, call_agent_seer_verify, call_agent_witch
from app.services.human_action_bridge import human_bridge

logger = logging.getLogger(__name__)

# P2 阶段 AI 行动间隔（秒），模拟思考时间提升观赏性
AI_ACTION_DELAY = 1.5  # AI 思考时间（秒），提升观赏性


async def night_start_node(state: GameFlowState) -> dict:
    """夜晚开始节点

    职责：
    - 如果是第一夜：current_round 已经是 1（初始状态设的）
    - 如果不是第一夜：current_round + 1
    - 重置所有夜晚临时数据为空/None
    - 记录 phase_change 事件

    返回更新字段：
    - current_round: 新回合号
    - 所有 night_* 字段重置
    """
    # 第一夜不 +1，后续夜晚 +1（白天结束时不加，夜晚开始时加）
    new_round = state["current_round"]
    if new_round > 0:
        new_round = state["current_round"] + 1
    else:
        new_round = 1

    await record_event(
        state["game_id"], new_round, "system", "phase_change",
        event_data={"phase": "night", "round": new_round},
    )

    # ─── 创建本轮 GameRound 记录（如不存在） ───
    from app.db.session import async_session_factory
    from app.models.game import GameRound
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(
            select(GameRound).where(
                GameRound.game_id == state["game_id"],
                GameRound.round_number == new_round,
            )
        )
        if not result.scalar_one_or_none():
            session.add(GameRound(
                game_id=state["game_id"],
                round_number=new_round,
            ))
            await session.commit()

    logger.info(f"[Night] 第 {new_round} 夜开始")
    await asyncio.sleep(0.3)  # 节奏控制

    # ─── 将上一轮的摘要追加到跨轮历史（供 AI 推理使用） ───
    game_history = list(state.get("game_history", []))
    prev_round = new_round - 1
    if prev_round >= 1:
        prev_speeches = list(state.get("speeches", []))
        prev_votes = dict(state.get("votes", {}))
        prev_deaths = list(state.get("night_deaths", []))
        prev_eliminated = state.get("eliminated_seat")
        # 只有当上一轮有实质内容时才记录
        if prev_speeches or prev_votes or prev_deaths or prev_eliminated is not None:
            round_summary = {
                "round": prev_round,
                "speeches": [{"seat": s["seat"], "content": s["content"]} for s in prev_speeches],
                "votes": {str(k): v for k, v in prev_votes.items()},
                "night_deaths": prev_deaths,
                "eliminated_seat": prev_eliminated,
            }
            game_history.append(round_summary)
            logger.info(f"[Night] 第 {prev_round} 轮摘要已加入游戏历史（共 {len(game_history)} 轮）")

    return {
        "current_round": new_round,
        # 重置夜晚临时数据
        "night_kill_target": None,
        "night_seer_target": None,
        "night_seer_result": None,
        "night_witch_action": "skip",
        "night_witch_target": None,
        "night_deaths": [],
        # 重置白天数据
        "speeches": [],
        "votes": {},
        "eliminated_seat": None,
        "is_pk": False,
        "pk_seats": [],
        # 更新跨轮历史
        "game_history": game_history,
    }


async def night_werewolf_node(state: GameFlowState) -> dict:
    """狼人行动节点

    职责：让存活的狼人选择击杀目标，处理双狼协商逻辑

    PRD 规则（E02）：
    - 2 狼人存活时分别选择，一致则执行，不一致时：
      - 双 AI → 随机选一个
      - 含人类 → 以人类选择为准
    - 只剩 1 狼人 → 直接执行其选择
    - 狼人不能击杀另一名狼人
    - 狼人必须选择目标（不能空刀）

    P2 实现：AI 随机选择存活非狼人玩家
    """
    # ─── 检查是否有存活狼人 ───
    alive_werewolves = get_alive_by_role(state["players"], PlayerRole.WEREWOLF)
    if not alive_werewolves:
        # 所有狼人已死亡，跳过狼人行动
        logger.info("[Night] 无存活狼人，跳过狼人行动")
        return {"night_kill_target": None}

    # ─── 获取可击杀目标（存活的非狼人玩家） ───
    alive_non_werewolf_seats = [
        p["seat_number"] for p in get_alive_players(state["players"])
        if p["role"] != PlayerRole.WEREWOLF
    ]

    if not alive_non_werewolf_seats:
        # 没有可击杀目标（极端情况：只剩狼人）
        logger.info("[Night] 无非狼人目标，跳过狼人行动")
        return {"night_kill_target": None}

    # ─── 狼人选择击杀目标 ───
    await asyncio.sleep(AI_ACTION_DELAY)  # 模拟思考时间
    choices = {}  # 预初始化，避免单狼分支未定义

    if len(alive_werewolves) == 1:
        sole_wolf = alive_werewolves[0]
        if sole_wolf["player_type"] == "human":
            # 人类独狼：等待人类提交击杀目标
            action = await human_bridge.wait_for_action(
                state["game_id"], "kill",
                {"seat_number": sole_wolf["seat_number"], "player_name": sole_wolf["player_name"], "role": sole_wolf["role"]},
            )
            target = action.get("target_seat")
            if target not in alive_non_werewolf_seats:
                target = call_agent_werewolf_kill(state, sole_wolf)
            agreement = "human_sole_werewolf"
        else:
            target = call_agent_werewolf_kill(state, sole_wolf)
            agreement = "single_ai_werewolf"
    else:
        # 2 个狼人分别选择
        choices = {}
        for ww in alive_werewolves:
            if ww["player_type"] == "human":
                # 人类狼人：等待提交击杀目标
                action = await human_bridge.wait_for_action(
                    state["game_id"], "kill",
                    {"seat_number": ww["seat_number"], "player_name": ww["player_name"], "role": ww["role"]},
                )
                choices[ww["seat_number"]] = action.get("target_seat")
            else:
                choices[ww["seat_number"]] = call_agent_werewolf_kill(state, ww)

        targets = list(choices.values())
        if len(set(targets)) == 1:
            # 选择一致
            target = targets[0]
            agreement = "unanimous"
        else:
            # 选择不一致 → 协商
            human_choices = [
                s for s, t in choices.items()
                if any(p["seat_number"] == s and p["player_type"] == "human" for p in state["players"])
            ]
            if human_choices:
                # 有人类狼人 → 人类优先
                target = choices[human_choices[0]]
                agreement = "human_priority"
            else:
                # 双 AI → 随机选一个目标
                target = random.choice(targets)
                agreement = "random"

    # ─── 记录事件 ───
    await record_event(
        state["game_id"], state["current_round"], "night", "night_kill",
        event_data={
            "target": target,
            "agreement": agreement,
            "choices": {str(k): v for k, v in choices.items()} if len(alive_werewolves) > 1 else {},
        },
    )

    target_name = next(
        (p["player_name"] for p in state["players"] if p["seat_number"] == target), "?"
    )
    logger.info(f"[Night] 狼人选择击杀 {target}号({target_name})，协商方式: {agreement}")

    return {"night_kill_target": target}


async def night_seer_node(state: GameFlowState) -> dict:
    """预言家行动节点

    职责：让存活的预言家选择查验一名玩家

    PRD 规则：
    - 预言家每轮可以查验一名存活的其他玩家
    - 不能查验自己
    - 可以重复查验已查验过的玩家
    - 结果只有两种：werewolf / villager（村民、女巫均返回"好人"→ 这里用 villager 表示）

    P2 实现：AI 随机选择存活的其他玩家查验
    """
    # ─── 检查预言家是否存活 ───
    seer = None
    for p in state["players"]:
        if p["role"] == PlayerRole.SEER and p["is_alive"]:
            seer = p
            break

    if not seer:
        logger.info("[Night] 预言家已死亡，跳过查验")
        return {"night_seer_target": None, "night_seer_result": None}

    # ─── 获取可查验目标（存活的其他玩家） ───
    alive_other_seats = [
        p["seat_number"] for p in get_alive_players(state["players"])
        if p["seat_number"] != seer["seat_number"]
    ]

    if not alive_other_seats:
        return {"night_seer_target": None, "night_seer_result": None}

    # ─── 预言家选择查验目标 ───
    await asyncio.sleep(AI_ACTION_DELAY)

    if seer["player_type"] == "human":
        action = await human_bridge.wait_for_action(
            state["game_id"], "verify",
            {"seat_number": seer["seat_number"], "player_name": seer["player_name"], "role": seer["role"]},
        )
        target = action.get("target_seat")
    else:
        target = call_agent_seer_verify(state, seer)

    # ─── 判定查验结果 ───
    target_player = next(p for p in state["players"] if p["seat_number"] == target)
    result = "werewolf" if target_player["role"] == PlayerRole.WEREWOLF else "villager"

    await record_event(
        state["game_id"], state["current_round"], "night", "night_verify",
        seat_number=seer["seat_number"],
        event_data={"target": target, "result": result},
    )

    logger.info(f"[Night] 预言家({seer['seat_number']}号)查验 {target}号 → {result}")

    # ─── 累积预言家查验历史（跨轮持久化） ───
    seer_history = list(state.get("seer_history", []))
    seer_history.append({
        "round": state["current_round"],
        "target": target,
        "result": result,
    })

    # ─── 私有通知：只发给预言家本人 ───
    if seer["player_type"] == "human":
        try:
            from app.api.ws_handler import ws_manager
            await ws_manager.broadcast(state["game_id"], {
                "type": "private_seer_result",
                "data": {
                    "target": target,
                    "result": result,
                    "round": state["current_round"],
                },
                "private_seat": seer["seat_number"],  # 前端用这个判断是否显示
            })
        except Exception as e:
            logger.debug(f"[Night] WS 私有通知失败: {e}")

    return {
        "night_seer_target": target,
        "night_seer_result": result,
        "seer_history": seer_history,
    }


async def night_witch_node(state: GameFlowState) -> dict:
    """女巫行动节点

    职责：让存活的女巫决定今晚的用药策略

    PRD 规则：
    - 女巫拥有一瓶解药和一瓶毒药，各只能用一次（整局）
    - 每晚只能使用一种药（互斥）：解药或毒药，不能同时用
    - 解药：救活当晚被狼人击杀的玩家
    - 毒药：指定一名存活玩家毒杀（不能对自己用）
    - 首夜女巫可以自救（被狼人击杀的是女巫自己时，可以用解药自救）
    - 不使用也是一种合法选择（skip）

    P2 实现：简单概率策略（解药 60-80%，毒药 20%，可 skip）
    """
    # ─── 检查女巫是否存活 ───
    witch = None
    for p in state["players"]:
        if p["role"] == PlayerRole.WITCH and p["is_alive"]:
            witch = p
            break

    if not witch:
        logger.info("[Night] 女巫已死亡，跳过女巫行动")
        return {"night_witch_action": "skip", "night_witch_target": None}

    # ─── 判断可用药水 ───
    save_available = not state["witch_save_used"] and state["night_kill_target"] is not None
    poison_available = not state["witch_poison_used"]

    # ─── 获取毒药可选目标（存活的其他玩家） ───
    alive_other_seats = [
        p["seat_number"] for p in get_alive_players(state["players"])
        if p["seat_number"] != witch["seat_number"]
    ]

    # ─── 女巫决策 ───
    await asyncio.sleep(AI_ACTION_DELAY)

    # ─── 私有通知：告诉女巫今晚谁被杀了（人类女巫需要此信息来决定是否用解药） ───
    if witch["player_type"] == "human":
        kill_target = state.get("night_kill_target")
        try:
            from app.api.ws_handler import ws_manager
            await ws_manager.broadcast(state["game_id"], {
                "type": "private_witch_info",
                "data": {
                    "night_kill_target": kill_target,
                    "save_available": save_available,
                    "poison_available": poison_available,
                },
                "private_seat": witch["seat_number"],
            })
        except Exception as e:
            logger.debug(f"[Night] WS 女巫私有通知失败: {e}")

    if witch["player_type"] == "human":
        human_action = await human_bridge.wait_for_action(
            state["game_id"], "save",
            {"seat_number": witch["seat_number"], "player_name": witch["player_name"], "role": witch["role"]},
        )
        action = human_action.get("action_type", "skip")
        target = human_action.get("target_seat")
        if action not in ("save", "poison", "skip"):
            action = "skip"
            target = None
    else:
        action, target = call_agent_witch(
            state, witch, save_available, poison_available, alive_other_seats
        )

    # ─── 校验决策合法性 ───
    if action == "save" and not save_available:
        action = "skip"
        target = None
    if action == "poison" and (not poison_available or target not in alive_other_seats):
        action = "skip"
        target = None

    await record_event(
        state["game_id"], state["current_round"], "night", 
        "night_save" if action == "save" else ("night_poison" if action == "poison" else "night_witch_skip"),
        seat_number=witch["seat_number"],
        event_data={"action": action, "target": target},
    )

    logger.info(f"[Night] 女巫({witch['seat_number']}号) 行动: {action}"
                f"{' 目标=' + str(target) if target else ''}")

    # ─── 更新药水使用状态 ───
    updates = {"night_witch_action": action, "night_witch_target": target}
    if action == "save":
        updates["witch_save_used"] = True
    elif action == "poison":
        updates["witch_poison_used"] = True

    return updates


async def night_settle_node(state: GameFlowState) -> dict:
    """夜晚结算节点

    职责：综合狼人击杀、女巫解药、女巫毒药三方行动，确定最终死亡名单

    结算规则：
    - 狼人击杀目标 → 如果女巫没用解药 → 死亡
    - 狼人击杀目标 → 如果女巫用了解药 → 存活（被救活）
    - 女巫毒药目标 → 必定死亡（毒药无法被解药抵消）
    - 同一人被击杀又被毒杀 → 死亡（只记录一次）
    - 由于女巫每晚只能用一种药，不存在"先救后毒同一人"的情况

    返回：更新后的 players（标记死亡）+ night_deaths 列表
    """
    night_deaths = []
    players = state["players"]

    # ─── 处理狼人击杀 ───
    kill_target = state["night_kill_target"]
    if kill_target is not None:
        if state["night_witch_action"] == "save":
            # 女巫用解药救了被击杀者 → 存活
            logger.info(f"[Settle] 女巫使用解药，{kill_target}号被救活")
        else:
            # 未被救 → 死亡
            night_deaths.append(kill_target)

    # ─── 处理女巫毒药 ───
    if state["night_witch_action"] == "poison" and state["night_witch_target"] is not None:
        poison_target = state["night_witch_target"]
        if poison_target not in night_deaths:
            night_deaths.append(poison_target)

    # ─── 更新玩家状态 ───
    for seat in night_deaths:
        reason = "killed_by_werewolf" if seat == kill_target else "poisoned"
        players = kill_player(players, seat, state["current_round"], "night", reason)

    # ─── 记录结算事件 ───
    await record_event(
        state["game_id"], state["current_round"], "night", "night_settle",
        event_data={
            "deaths": night_deaths,
            "kill_target": kill_target,
            "witch_action": state["night_witch_action"],
            "witch_target": state["night_witch_target"],
        },
    )

    if night_deaths:
        logger.info(f"[Settle] 夜晚死亡: {night_deaths}")
    else:
        logger.info("[Settle] 平安夜，无人死亡")

    return {
        "night_deaths": night_deaths,
        "players": players,
        "post_victory_route": "after_night",  # 告诉路由函数：这是夜晚后的胜负检查
    }
