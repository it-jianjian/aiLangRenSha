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

from app.models.game import PlayerRole
from app.graphs.state import GameFlowState
from app.graphs.event_bus import (
    record_event,
    get_alive_players,
    get_alive_by_role,
    kill_player,
)
from app.graphs.nodes.agent_nodes import call_agent_werewolf_kill, call_agent_seer_verify, call_agent_witch, call_agent_guard, call_agent_hunter_shoot
from app.services.human_action_bridge import human_bridge
from app.services.game_rules import can_hunter_shoot, guard_target_is_valid, resolve_night_deaths

logger = logging.getLogger(__name__)

# P2 阶段 AI 行动间隔（秒），模拟思考时间提升观赏性
AI_ACTION_DELAY = 1.5  # AI 思考时间（秒），提升观赏性


async def _update_game_round_summary(game_id: str, round_number: int, **values) -> None:
    from app.db.session import async_session_factory
    from app.models.game import GameRound
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(
            select(GameRound).where(GameRound.game_id == game_id, GameRound.round_number == round_number)
        )
        game_round = result.scalar_one_or_none()
        if game_round is None:
            game_round = GameRound(game_id=game_id, round_number=round_number)
            session.add(game_round)
        for key, value in values.items():
            setattr(game_round, key, value)
        await session.commit()


async def night_start_node(state: GameFlowState) -> dict:
    """夜晚开始节点

    职责：
    - 如果是第一夜：current_round 已经是 1（初始状态设的）
    - 如果不是第一夜：current_round + 1
    - 重置所有夜晚临时数据为空/None
    - 记录唯一可公开的 night_phase 阶段边界事件

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
        state["game_id"], new_round, "night", "night_phase",
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
        prev_is_pk = state.get("is_pk", False)
        prev_pk_seats = list(state.get("pk_seats", []))
        prev_pre_pk_votes = dict(state.get("pre_pk_votes", {}))
        prev_pk_speeches = list(state.get("pk_speeches", []))
        # 只有当上一轮有实质内容时才记录
        if prev_speeches or prev_votes or prev_deaths or prev_eliminated is not None:
            round_summary = {
                "round": prev_round,
                "speeches": [{"seat": s["seat"], "content": s["content"]} for s in prev_speeches],
                "votes": {str(k): v for k, v in prev_votes.items()},
                "night_deaths": prev_deaths,
                "eliminated_seat": prev_eliminated,
            }
            # 平票 PK 详情：让 AI 能记忆“上轮出现了平票、两人 battle 及当时的投票”
            if prev_is_pk:
                round_summary["is_pk"] = True
                round_summary["pk_seats"] = prev_pk_seats
                round_summary["pre_pk_votes"] = {
                    str(k): v for k, v in prev_pre_pk_votes.items()
                }
                round_summary["pk_speeches"] = [
                    {"seat": s["seat"], "content": s["content"]} for s in prev_pk_speeches
                ]
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
        "night_guard_target": None,
        "night_deaths": [],
        "pending_hunter_shot": None,
        # 重置白天数据
        "speeches": [],
        "votes": {},
        "eliminated_seat": None,
        "is_pk": False,
        "pk_seats": [],
        "pre_pk_votes": {},
        "pk_speeches": [],
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
                {"seat_number": sole_wolf["seat_number"], "player_name": sole_wolf["player_name"], "role": sole_wolf["role"],
                 "phase": "night", "allowed_target_seats": alive_non_werewolf_seats},
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
                    {"seat_number": ww["seat_number"], "player_name": ww["player_name"], "role": ww["role"],
                     "phase": "night", "allowed_target_seats": alive_non_werewolf_seats},
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
                # 双 AI → 稳定仲裁（较小座位狼人优先），避免正常主路径随机。
                target = choices[sorted(choices)[0]]
                agreement = "stable_ai_priority"

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
            {"seat_number": seer["seat_number"], "player_name": seer["player_name"], "role": seer["role"],
             "phase": "night", "allowed_target_seats": alive_other_seats},
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
            await ws_manager.send_to_seat(state["game_id"], seer["seat_number"], {
                "type": "private_seer_result",
                "data": {
                    "target": target,
                    "result": result,
                    "round": state["current_round"],
                },
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
            await ws_manager.send_to_seat(state["game_id"], witch["seat_number"], {
                "type": "private_witch_info",
                "data": {
                    "night_kill_target": kill_target,
                    "save_available": save_available,
                    "poison_available": poison_available,
                },
            })
        except Exception as e:
            logger.debug(f"[Night] WS 女巫私有通知失败: {e}")

    if witch["player_type"] == "human":
        human_action = await human_bridge.wait_for_action(
            state["game_id"], "save",
            {"seat_number": witch["seat_number"], "player_name": witch["player_name"], "role": witch["role"],
             "phase": "night", "allowed_target_seats": alive_other_seats,
             "empty_target_actions": ["save", "skip"],
             "extra": {
                 "night_kill_target": state.get("night_kill_target"),
                 "save_available": save_available,
                 "poison_available": poison_available,
             }},
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


async def night_guard_node(state: GameFlowState) -> dict:
    """守卫行动：存活守卫每夜守护一名存活玩家，不能连续守同一目标。"""
    guard = next((p for p in state["players"] if p["role"] == PlayerRole.GUARD and p["is_alive"]), None)
    if guard is None:
        return {"night_guard_target": None}
    alive_seats = [p["seat_number"] for p in get_alive_players(state["players"])]
    candidates = [seat for seat in alive_seats if seat != state.get("guard_last_target")]
    if guard["player_type"] == "human":
        action = await human_bridge.wait_for_action(state["game_id"], "guard", {
            "seat_number": guard["seat_number"], "role": "guard", "phase": "night",
            "last_target": state.get("guard_last_target"), "allowed_target_seats": candidates,
        })
        target = action.get("target_seat")
    else:
        target = call_agent_guard(state, guard, candidates)
    valid, _ = guard_target_is_valid(target, state.get("guard_last_target"), alive_seats) if target is not None else (False, "")
    if not valid:
        # AI 兜底可跳过；人类请求在 bridge 中已被服务端拒绝，不能静默降级。
        target = None
    await record_event(state["game_id"], state["current_round"], "night", "night_guard", seat_number=guard["seat_number"], event_data={"target": target})
    await _update_game_round_summary(state["game_id"], state["current_round"], guard_target_seat=target)
    return {"night_guard_target": target, "guard_last_target": target or state.get("guard_last_target")}


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
    death_causes = resolve_night_deaths(
        state["night_kill_target"], state["night_witch_action"], state["night_witch_target"], state.get("night_guard_target"),
    )
    night_deaths = sorted(death_causes)
    players = state["players"]

    kill_target = state["night_kill_target"]

    # ─── 更新玩家状态 ───
    for seat in night_deaths:
        reason = death_causes[seat]
        players = kill_player(players, seat, state["current_round"], "night", reason)

    pending_hunter_shot = None
    for seat in night_deaths:
        dead = next((p for p in players if p["seat_number"] == seat), None)
        if dead and dead.get("role") == PlayerRole.HUNTER and can_hunter_shoot(dead.get("death_reason")):
            pending_hunter_shot = {"seat_number": seat, "trigger": dead.get("death_reason"), "phase": "night"}
            break

    # ─── 记录结算事件 ───
    await record_event(
        state["game_id"], state["current_round"], "night", "night_settle",
        event_data={
            "deaths": night_deaths,
            "kill_target": kill_target,
            "witch_action": state["night_witch_action"],
            "witch_target": state["night_witch_target"],
            "guard_target": state.get("night_guard_target"),
            "death_causes": death_causes,
        },
    )

    if night_deaths:
        logger.info(f"[Settle] 夜晚死亡: {night_deaths}")
    else:
        logger.info("[Settle] 平安夜，无人死亡")

    return {
        "night_deaths": night_deaths,
        "players": players,
        "pending_hunter_shot": pending_hunter_shot,
        "post_victory_route": "after_night",  # 告诉路由函数：这是夜晚后的胜负检查
    }


async def hunter_revenge_node(state: GameFlowState) -> dict:
    """仅消费显式 pending_hunter_shot；毒杀绝不触发，不扫描本轮死者推导资格。"""
    pending = state.get("pending_hunter_shot")
    if isinstance(pending, dict):
        pending_seat = pending.get("seat_number")
        pending_trigger = pending.get("trigger")
    elif pending is not None:
        pending_seat = pending
        pending_trigger = None
    else:
        # 未显式设置待开枪状态，不隐式推导资格
        return {"pending_hunter_shot": None}
    hunter = next((p for p in state["players"] if p["role"] == PlayerRole.HUNTER and p["seat_number"] == pending_seat), None)
    if hunter is None or not can_hunter_shoot(hunter.get("death_reason")):
        return {"pending_hunter_shot": None}
    alive = [p["seat_number"] for p in get_alive_players(state["players"]) if p["seat_number"] != hunter["seat_number"]]
    if not alive:
        return {"pending_hunter_shot": None}
    if hunter["player_type"] == "human":
        action = await human_bridge.wait_for_action(state["game_id"], "hunter_shoot", {
            "seat_number": hunter["seat_number"], "role": "hunter",
            "phase": hunter.get("death_phase") or "night", "allowed_target_seats": alive,
            "empty_target_actions": ["hunter_shoot"],
        }, timeout_seconds=60)
        target = action.get("target_seat")
    else:
        target = call_agent_hunter_shoot(state, hunter, alive, pending_trigger or hunter.get("death_reason"))
    if target not in alive:
        target = None
    if target is not None:
        phase = hunter.get("death_phase") or "night"
        players = kill_player(state["players"], target, state["current_round"], phase, "hunter_shot")
        await record_event(state["game_id"], state["current_round"], phase, "hunter_shot", seat_number=hunter["seat_number"], event_data={"target": target, "trigger": hunter["death_reason"]})
        await _update_game_round_summary(state["game_id"], state["current_round"], hunter_shot_seat=target, hunter_shot_trigger=hunter["death_reason"])
        return {"players": players, "pending_hunter_shot": None}
    await record_event(state["game_id"], state["current_round"], hunter.get("death_phase") or "night", "hunter_revenge", seat_number=hunter["seat_number"], event_data={"result": "skip", "trigger": hunter["death_reason"]})
    await _update_game_round_summary(state["game_id"], state["current_round"], hunter_shot_seat=None, hunter_shot_trigger=hunter["death_reason"])
    return {"pending_hunter_shot": None}
