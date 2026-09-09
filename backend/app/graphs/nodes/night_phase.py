"""AI 狼人杀 — 夜晚阶段节点函数

本文件包含夜晚阶段的节点函数：
1. night_start_node           — 夜晚开始，重置夜晚临时数据，轮次+1
2. night_parallel_actions_node — 阶段 1 并行节点：狼人+预言家+守卫同时行动
3. night_witch_node           — 女巫行动：依赖 night_kill_target，串行执行
4. night_settle_node          — 夜晚结算：综合三方行动，确定最终死亡名单

调用链：night_start → night_parallel_actions → night_witch → night_settle → victory_check

PRD 规则要点：
- 狼人必须击杀目标（不能空刀），2 狼不一致时 AI 随机/人类优先
- 预言家可查验任何存活的其他玩家（不能验自己）
- 女巫每晚只能用一种药（解药/毒药互斥），各限用一次（整局）
- 首夜女巫可以自救
"""

import asyncio
import json
import logging

from app.agent.decision_parser import validate_decision
from app.config import get_settings
from app.graphs.event_bus import (
    get_alive_by_role,
    get_alive_players,
    kill_player,
    record_event,
)
from app.graphs.nodes import timed_node
from app.graphs.nodes.agent_nodes import (
    call_agent_async,
    call_agent_guard,
    call_agent_hunter_shoot,
    call_agent_kill_proposal,
    call_agent_werewolf_kill,
    call_agent_witch,
)
from app.graphs.state import GameFlowState
from app.models.game import PlayerRole
from app.services.game_rules import can_hunter_shoot, guard_target_is_valid, resolve_night_deaths
from app.services.human_action_bridge import human_bridge

logger = logging.getLogger(__name__)


async def _summarize_history_if_needed(game_history: list[dict], new_round: int) -> list[dict]:
    """当 game_history 序列化长度超阈值时，对"距今 2 轮之前"的历史压缩为摘要。

    摘要用小模型（复用 llm_action_simple_model），失败保留原文。
    已标记 summarized: true 的轮次不再重复压缩。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.agent.llm import create_llm
    from app.config import get_settings

    settings = get_settings()
    budget = settings.prompt_history_budget
    if budget <= 0:
        return game_history

    # 序列化长度检查
    serialized = json.dumps(game_history, ensure_ascii=False)
    if len(serialized) <= budget:
        return game_history

    # 找出需要压缩的轮次（距今 2 轮之前，且未被压缩过）
    cutoff_round = new_round - 2  # > cutoff_round 的保持全量
    old_entries = [h for h in game_history if h.get("round", 0) <= cutoff_round and not h.get("summarized")]
    if not old_entries:
        return game_history

    # 创建 LLM（优先小模型）
    llm = create_llm(action_type="speech")  # 用阶段 3 的简单模型路由（speech 走大模型）

    for entry in old_entries:
        speeches = entry.get("speeches", [])
        if not speeches:
            entry["summarized"] = True
            continue

        # 构建压缩 Prompt
        speech_text = "\n".join(f"  {s['seat']}号: {s['content']}" for s in speeches)
        prompt = (
            f"请将以下狼人杀第{entry['round']}轮的发言压缩为 3~5 句摘要。\n"
            f"要求：保留关键事实（谁跳了什么身份、指控关系、票型结果、死亡），去除修辞与重复。\n"
            f"直接输出摘要文本，不要 JSON 壳。\n\n"
            f"【第{entry['round']}轮发言】\n{speech_text}"
        )

        try:
            response = llm.invoke([
                SystemMessage(content="你是游戏记录助手，擅长将长对话精简为关键要点。"),
                HumanMessage(content=prompt),
            ])
            summary_text = response.content if hasattr(response, "content") else str(response)
            # 替换 speeches 为摘要
            entry["speeches"] = [{"seat": 0, "content": summary_text.strip(), "summarized": True}]
            entry["summarized"] = True
            logger.info(f"[Summary] 第 {entry['round']} 轮已压缩为 {len(summary_text)} 字符摘要")
        except Exception as e:
            logger.warning(f"[Summary] 第 {entry['round']} 轮压缩失败，保留原文: {e}")
            # 仍标记为 summarized 避免重复尝试
            entry["summarized"] = True

    return game_history


async def _update_game_round_summary(game_id: str, round_number: int, **values) -> None:
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.game import GameRound

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


@timed_node
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
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.game import GameRound

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

    # ─── Prompt 膨胀治理（阶段 4a）：超长历史压缩为滚动摘要 ───
    game_history = await _summarize_history_if_needed(game_history, new_round)

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


@timed_node
async def night_parallel_actions_node(state: GameFlowState) -> dict:
    """阶段 1 并行节点：狼人+预言家+守卫同时行动

    狼人、预言家、守卫三方行动互相独立，通过 asyncio.gather 并行执行。
    女巫在下一节点串行执行（依赖 night_kill_target）。

    双狼协商语义不变：并行收集意向后按现有规则仲裁。
    """
    settings = get_settings()

    # ─── 准备狼人行动 ───
    alive_werewolves = get_alive_by_role(state["players"], PlayerRole.WEREWOLF)
    alive_non_werewolf_seats = [
        p["seat_number"] for p in get_alive_players(state["players"])
        if p["role"] != PlayerRole.WEREWOLF
    ]

    # ─── 准备预言家行动 ───
    seer = next((p for p in state["players"] if p["role"] == PlayerRole.SEER and p["is_alive"]), None)
    seer_alive_other = [
        p["seat_number"] for p in get_alive_players(state["players"])
        if p["seat_number"] != seer["seat_number"]
    ] if seer else []

    # ─── 准备守卫行动 ───
    guard = next((p for p in state["players"] if p["role"] == PlayerRole.GUARD and p["is_alive"]), None)
    guard_candidates = [
        seat for seat in [p["seat_number"] for p in get_alive_players(state["players"])]
        if seat != state.get("guard_last_target")
    ] if guard else []

    # ─── 并行执行三方行动 ───
    tasks = []

    if alive_werewolves and alive_non_werewolf_seats:
        tasks.append(_do_werewolf(state, alive_werewolves, alive_non_werewolf_seats))
    else:
        tasks.append(_no_op({"night_kill_target": None, "werewolf_agreement": "skip"}))
        if not alive_werewolves:
            logger.info("[Night] 无存活狼人，跳过狼人行动")
        if not alive_non_werewolf_seats:
            logger.info("[Night] 无非狼人目标，跳过狼人行动")

    if seer and seer_alive_other:
        tasks.append(_do_seer(state, seer, seer_alive_other))
    else:
        tasks.append(_no_op({"night_seer_target": None, "night_seer_result": None}))
        if not seer:
            logger.info("[Night] 预言家已死亡，跳过查验")

    if guard and guard_candidates:
        tasks.append(_do_guard(state, guard, guard_candidates))
    else:
        tasks.append(_no_op({"night_guard_target": None}))

    results = await asyncio.gather(*tasks)

    # 合并三方行动结果
    merged = {}
    for r in results:
        merged.update(r)

    # ─── 并行完成后统一 delay ───
    if settings.ai_action_delay_night > 0:
        await asyncio.sleep(settings.ai_action_delay_night)

    return merged


async def _do_werewolf(state, alive_werewolves, alive_non_werewolf_seats) -> dict:
    """狼人击杀行动分派器（可被 gather 并行调用）。

    - 关闭协商（默认）或非双狼 → 走原样抽出的 legacy 分支，行为与改造前字节级一致（验收2）
    - 开启协商且恰好双狼 → 走 _do_werewolf_deliberate；协商异常 → 退回 legacy(fallback=True)（FR-4）
    """
    settings = get_settings()
    if not settings.wolf_deliberation_enabled or len(alive_werewolves) != 2:
        return await _do_werewolf_legacy(state, alive_werewolves, alive_non_werewolf_seats)
    try:
        return await _do_werewolf_deliberate(state, alive_werewolves, alive_non_werewolf_seats)
    except Exception as e:
        logger.warning(f"[Night] 狼队协商异常，退回盲投仲裁: {e}")
        return await _do_werewolf_legacy(state, alive_werewolves, alive_non_werewolf_seats, fallback=True)


async def _do_werewolf_legacy(state, alive_werewolves, alive_non_werewolf_seats, fallback=False) -> dict:
    """狼人击杀行动（改造前逻辑原样抽出；关闭协商时的默认路径）。

    fallback=True 表示由协商异常降级而来，日志追加 [deliberation_fallback] 标记（FR-4/验收4）；
    fallback=False 时与改造前逐字节一致。
    """
    choices = {}

    if len(alive_werewolves) == 1:
        sole_wolf = alive_werewolves[0]
        if sole_wolf["player_type"] == "human":
            action = await human_bridge.wait_for_action(
                state["game_id"], "kill",
                {"seat_number": sole_wolf["seat_number"], "player_name": sole_wolf["player_name"],
                 "role": sole_wolf["role"], "phase": "night",
                 "allowed_target_seats": alive_non_werewolf_seats},
            )
            target = action.get("target_seat")
            if target not in alive_non_werewolf_seats:
                target = call_agent_werewolf_kill(state, sole_wolf)
            agreement = "human_sole_werewolf"
        else:
            target = await call_agent_async(state, sole_wolf, "kill")
            agreement = "single_ai_werewolf"
    else:
        # 双狼并行收集
        wolf_tasks = []
        for ww in alive_werewolves:
            if ww["player_type"] == "human":
                wolf_tasks.append(_human_wolf_choice(state, ww, alive_non_werewolf_seats))
            else:
                wolf_tasks.append(_ai_wolf_choice(state, ww))

        wolf_results = await asyncio.gather(*wolf_tasks)
        for seat, choice in wolf_results:
            choices[seat] = choice

        targets = list(choices.values())
        if len(set(targets)) == 1:
            target = targets[0]
            agreement = "unanimous"
        else:
            human_seats = [
                s for s, t in choices.items()
                if any(p["seat_number"] == s and p["player_type"] == "human" for p in state["players"])
            ]
            if human_seats:
                target = choices[human_seats[0]]
                agreement = "human_priority"
            else:
                target = choices[sorted(choices)[0]]
                agreement = "stable_ai_priority"

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
    marker = " [deliberation_fallback]" if fallback else ""
    logger.info(f"[Night] 狼人选择击杀 {target}号({target_name})，协商方式: {agreement}{marker}")

    return {"night_kill_target": target, "werewolf_agreement": agreement}


async def _do_werewolf_deliberate(state, alive_werewolves, alive_non_werewolf_seats) -> dict:
    """狼队协商（需求一 FR-1/FR-2）：一轮亮牌讨论 + 再表态 + 仲裁兜底。

    仅在 wolf_deliberation_enabled=True 且恰好 2 名存活狼人时进入。收尾与 legacy 完全一致
    （night_kill 事件 + {night_kill_target, werewolf_agreement}），下游女巫/结算零改动。
    """
    targets = alive_non_werewolf_seats
    ai_wolves = [w for w in alive_werewolves if w["player_type"] != "human"]
    human_wolves = [w for w in alive_werewolves if w["player_type"] == "human"]

    # ─── 双人类狼：沿用现状双提交 + human_priority，不加协商流程（FR-2） ───
    if not ai_wolves:
        return await _do_werewolf_legacy(state, alive_werewolves, alive_non_werewolf_seats)

    # ─── AI + human：AI 先表态 → 私密推给人类 → 人类最终决策（FR-2） ───
    if human_wolves:
        ai_prop = await _ai_proposal(state, ai_wolves[0], targets, deliberation_round=1)
        await _record_negotiation(state, stage=1, proposals=[ai_prop])
        human_seat, human_target = await _human_wolf_choice(
            state, human_wolves[0], targets,
            extra={"teammate_suggestion": {
                "seat": ai_prop["seat"], "target": ai_prop["target"], "reason": ai_prop["reason"],
            }},
        )
        choices = {ai_prop["seat"]: ai_prop["target"], human_seat: human_target}
        # 人类即最终决策，不要求二次表态；越界兜底回 AI 目标
        target = human_target if human_target in targets else ai_prop["target"]
        return await _finalize_kill(state, target, "human_priority", choices)

    # ─── 双 AI：完整协商矩阵（FR-1） ───
    wolf_a, wolf_b = ai_wolves[0], ai_wolves[1]

    # 轮 1：各自独立表态（并行）
    r1 = await asyncio.gather(
        _ai_proposal(state, wolf_a, targets, deliberation_round=1),
        _ai_proposal(state, wolf_b, targets, deliberation_round=1),
    )
    await _record_negotiation(state, stage=1, proposals=list(r1))
    if r1[0]["target"] == r1[1]["target"]:
        return await _finalize_kill(
            state, r1[0]["target"], "unanimous_first",
            {r1[0]["seat"]: r1[0]["target"], r1[1]["seat"]: r1[1]["target"]},
        )

    # 轮 2：交换同伴 {target, reason}，各自修订或坚持（并行）
    r2 = await asyncio.gather(
        _ai_proposal(state, wolf_a, targets, companion=r1[1], deliberation_round=2),
        _ai_proposal(state, wolf_b, targets, companion=r1[0], deliberation_round=2),
    )
    await _record_negotiation(state, stage=2, proposals=list(r2))
    choices = {r2[0]["seat"]: r2[0]["target"], r2[1]["seat"]: r2[1]["target"]}
    if r2[0]["target"] == r2[1]["target"]:
        return await _finalize_kill(state, r2[0]["target"], "converged_after_debate", choices)

    # 仍分歧 → 现行仲裁 AI 分支（座位号最小者优先，与 legacy stable_ai_priority 一致）
    return await _finalize_kill(state, choices[sorted(choices)[0]], "stable_ai_priority", choices)


async def _ai_proposal(state, wolf, targets, companion=None, deliberation_round=1) -> dict:
    """单个 AI 狼人协商表态：取回 {target, reason} 并补 validate_decision 校验（FR-1③）。

    非法/超时/解析失败 → 取第一个合法非狼目标兜底并标记 is_fallback。
    """
    res = await call_agent_kill_proposal(
        state, wolf,
        extra_briefing=_briefing(companion) if companion else None,
        deliberation_round=deliberation_round,
    )
    alive_seats = [p["seat_number"] for p in get_alive_players(state["players"])]
    valid, _ = validate_decision(
        decision=res["target"], action_type="kill",
        alive_seats=alive_seats, own_seat=wolf["seat_number"],
        werewolf_seats=state.get("werewolf_seats", []),
        allowed_target_seats=targets,
    )
    target = res["target"]
    is_fallback = bool(res.get("is_fallback"))
    if not valid:
        target = targets[0] if targets else None
        is_fallback = True
    return {"seat": wolf["seat_number"], "target": target,
            "reason": res.get("reason", ""), "is_fallback": is_fallback}


def _briefing(companion: dict | None) -> str | None:
    """拼装同伴亮牌文本（仅含同伴 target+reason，不含任何非狼信息，FR-1②）。"""
    if not companion:
        return None
    return (
        f"【狼队协商·同伴亮牌】你的狼人同伴 {companion['seat']}号 建议击杀 "
        f"{companion['target']}号，理由：{companion.get('reason') or '（未提供）'}。\n"
        f"请据此修订或坚持你的击杀目标，并给出你的理由。"
    )


async def _record_negotiation(state, stage: int, proposals: list[dict]) -> None:
    """记录协商事件（私有）+ 定向投递给狼队座位（FR-3 隔离红线）。

    - phase="night" 且 event_type != "night_phase" → to_public_event 返回 None → 绝不进公共广播
    - send_to_seat 仅遍历 werewolf_seats → 点对点，非狼座位永不接收
    """
    payload = {
        "stage": stage,
        "proposals": {
            str(p["seat"]): {"target": p["target"], "reason": p.get("reason", "")}
            for p in proposals
        },
    }
    await record_event(
        state["game_id"], state["current_round"], "night", "werewolf_negotiation",
        event_data=payload,
    )
    try:
        from app.api.ws_handler import ws_manager
        for wseat in state.get("werewolf_seats", []):
            await ws_manager.send_to_seat(state["game_id"], wseat, {
                "type": "werewolf_negotiation",
                "data": payload,
            })
    except Exception as e:
        logger.debug(f"[Deliberation] 协商定向投递失败: {e}")


async def _finalize_kill(state, target, agreement: str, choices: dict) -> dict:
    """协商收尾：记录 night_kill 事件 + 返回下游状态（形状与 legacy 一致）。"""
    await record_event(
        state["game_id"], state["current_round"], "night", "night_kill",
        event_data={
            "target": target,
            "agreement": agreement,
            "choices": {str(k): v for k, v in choices.items()},
        },
    )
    target_name = next(
        (p["player_name"] for p in state["players"] if p["seat_number"] == target), "?"
    )
    logger.info(f"[Night] 狼人选择击杀 {target}号({target_name})，协商方式: {agreement}")
    return {"night_kill_target": target, "werewolf_agreement": agreement}


async def _human_wolf_choice(state, wolf, targets, extra=None):
    """人类狼人提交击杀目标。

    extra: 可选附加信息（混合模式下为队友刀书 teammate_suggestion），透传进
    human_action_prompt.data.extra；断线重连经 get_pending_action 也能恢复（决策 D5）。
    """
    player_info = {
        "seat_number": wolf["seat_number"], "player_name": wolf["player_name"],
        "role": wolf["role"], "phase": "night", "allowed_target_seats": targets,
    }
    if extra:
        player_info["extra"] = extra
    action = await human_bridge.wait_for_action(state["game_id"], "kill", player_info)
    return (wolf["seat_number"], action.get("target_seat"))


async def _ai_wolf_choice(state, wolf):
    """AI 狼人提交击杀目标"""
    target = await call_agent_async(state, wolf, "kill")
    return (wolf["seat_number"], target)


async def _do_seer(state, seer, alive_other_seats) -> dict:
    """预言家查验行动（可被 gather 并行调用）"""
    if seer["player_type"] == "human":
        action = await human_bridge.wait_for_action(
            state["game_id"], "verify",
            {"seat_number": seer["seat_number"], "player_name": seer["player_name"],
             "role": seer["role"], "phase": "night", "allowed_target_seats": alive_other_seats},
        )
        target = action.get("target_seat")
    else:
        target = await call_agent_async(state, seer, "verify")

    # 兜底：target 无效时选第一个合法目标
    if target is None or target not in alive_other_seats:
        target = alive_other_seats[0] if alive_other_seats else None
    if target is None:
        return {"night_seer_target": None, "night_seer_result": None}

    target_player = next((p for p in state["players"] if p["seat_number"] == target), None)
    if target_player is None:
        return {"night_seer_target": None, "night_seer_result": None}
    result = "werewolf" if target_player["role"] == PlayerRole.WEREWOLF else "villager"

    await record_event(
        state["game_id"], state["current_round"], "night", "night_verify",
        seat_number=seer["seat_number"],
        event_data={"target": target, "result": result},
    )

    logger.info(f"[Night] 预言家({seer['seat_number']}号)查验 {target}号 → {result}")

    seer_history = list(state.get("seer_history", []))
    seer_history.append({
        "round": state["current_round"],
        "target": target,
        "result": result,
    })

    if seer["player_type"] == "human":
        try:
            from app.api.ws_handler import ws_manager
            await ws_manager.send_to_seat(state["game_id"], seer["seat_number"], {
                "type": "private_seer_result",
                "data": {"target": target, "result": result, "round": state["current_round"]},
            })
        except Exception as e:
            logger.debug(f"[Night] WS 私有通知失败: {e}")

    return {
        "night_seer_target": target,
        "night_seer_result": result,
        "seer_history": seer_history,
    }


async def _do_guard(state, guard, candidates) -> dict:
    """守卫守护行动（可被 gather 并行调用）"""
    if guard["player_type"] == "human":
        action = await human_bridge.wait_for_action(state["game_id"], "guard", {
            "seat_number": guard["seat_number"], "role": "guard", "phase": "night",
            "last_target": state.get("guard_last_target"), "allowed_target_seats": candidates,
        })
        target = action.get("target_seat")
    else:
        target = await call_agent_async(state, guard, "guard", None)

    valid, _ = guard_target_is_valid(target, state.get("guard_last_target"),
                                      [p["seat_number"] for p in get_alive_players(state["players"])]) if target is not None else (False, "")
    if not valid:
        target = None

    await record_event(state["game_id"], state["current_round"], "night", "night_guard",
                       seat_number=guard["seat_number"], event_data={"target": target})

    await _update_game_round_summary(state["game_id"], state["current_round"], guard_target_seat=target)

    return {"night_guard_target": target, "guard_last_target": target or state.get("guard_last_target")}


async def _no_op(result):
    """空操作占位（当某角色不存在或无法行动时）"""
    return result


@timed_node
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

    settings = get_settings()

    # ─── 判断可用药水 ───
    save_available = not state["witch_save_used"] and state["night_kill_target"] is not None
    poison_available = not state["witch_poison_used"]

    # ─── 获取毒药可选目标（存活的其他玩家） ───
    alive_other_seats = [
        p["seat_number"] for p in get_alive_players(state["players"])
        if p["seat_number"] != witch["seat_number"]
    ]

    # ─── 女巫决策 ───
    if settings.ai_action_delay_night > 0:
        await asyncio.sleep(settings.ai_action_delay_night)

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


@timed_node
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


@timed_node
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


@timed_node
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
