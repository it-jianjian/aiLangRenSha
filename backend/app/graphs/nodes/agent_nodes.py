"""AI 狼人杀 — Agent 桥接节点

职责：在游戏流程节点中调用 Agent 推理子图，替代 P2 阶段的随机决策

调用链：night/day/vote node → call_agent_werewolf/seer/witch/villager() → decision

设计说明：
- 每个角色有专用封装函数，使调用方代码更清晰
- 底层统一调用 run_agent()，专用封装负责角色特定的前后处理
- call_agent() 是通用入口，专用函数在它之上添加语义和校验
- 阶段 A1：决策类（kill/verify/vote/guard/poison/hunter_shoot）走 ReAct 子图
"""

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from app.graphs.agent_graph import run_agent

logger = logging.getLogger(__name__)

# ─── 阶段 1：异步包装器 ─────────────────────────────────────
# run_agent 是同步函数，用 asyncio.to_thread 包装后可用于 asyncio.gather 并行
# 模块级 Semaphore 限制 LLM 并发 QPS，防止聚合平台限流
_llm_semaphore: asyncio.Semaphore | None = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(5)
    return _llm_semaphore


async def call_agent_async(
    game_state: dict[str, Any],
    player: dict[str, Any],
    action_type: str,
    llm: Any = None,
) -> Any:
    """异步 Agent 调用入口

    决策类行动（kill/verify/vote/guard/poison/hunter_shoot）走 ReAct 子图（A1）；
    其他类型（speech/last_words）走流式单次调用。
    """
    if action_type in REACT_ACTION_TYPES and llm is None:
        try:
            # ReAct 路径：按需查询工具
            return await call_agent_react(game_state, player, action_type)
        except Exception as e:
            logger.warning(f"[Agent] {player['seat_number']}号 ReAct 失败，走全量fallback: {e}")
            # 降级：走全量单次调用
            async with _get_llm_semaphore():
                return await asyncio.to_thread(call_agent, game_state, player, action_type, None)

    # 原有路径：单次 LLM 调用
    async with _get_llm_semaphore():
        return await asyncio.to_thread(call_agent, game_state, player, action_type, llm)


async def call_agent_stream(
    game_state: dict[str, Any],
    player: dict[str, Any],
    action_type: str,
    on_chunk: Callable[[str], None],
    llm: Any = None,
) -> str:
    """流式调用 Agent 推理，逐 chunk 回调

    参数:
        on_chunk: 每个文本 chunk 的回调函数（同步）
    返回:
        完整文本
    """
    from app.agent.context_filter import filter_context
    from app.agent.llm import MockWerewolfLLM, create_llm
    from app.agent.prompts import build_agent_prompt

    seat = player["seat_number"]
    role = getattr(player["role"], "value", player["role"])

    # 构建消息（同步）
    filtered = filter_context(game_state, seat, role, action_type)
    messages = build_agent_prompt(role=role, seat_number=seat, action_type=action_type, game_context=filtered)

    llm_instance = llm or create_llm(seat_number=seat, action_type=action_type)

    # Mock 不走流式
    if isinstance(llm_instance, MockWerewolfLLM):
        result = await asyncio.to_thread(run_agent, {
            "seat_number": seat,
            "role": role,
            "player_name": player.get("player_name", f"{seat}号"),
            "action_type": action_type,
            "game_context": game_state,
            "llm": llm_instance,
        })
        text = result["decision"]
        if text:
            on_chunk(text)
        return text if isinstance(text, str) else ""

    # 流式调用
    full_text = ""
    try:
        async with _get_llm_semaphore():
            async for chunk in llm_instance.astream(messages):
                delta = chunk.content if hasattr(chunk, "content") else str(chunk)
                if delta:
                    full_text += delta
                    on_chunk(delta)
    except Exception as e:
        logger.warning(f"[Agent] {seat}号 流式调用失败: {e}")
        if not full_text:
            # 回退到同步调用
            result = await call_agent_async(game_state, player, action_type, llm)
            full_text = result["decision"] if isinstance(result, dict) else str(result)

    # 兜底：若模型惯性输出 JSON 包裹（```json ...```），提取 decision 字段作为发言
    # （实时 chunk 已推送无法撤回，但落库/回放使用正确文本）
    if full_text:
        from app.agent.decision_parser import parse_decision
        parsed = parse_decision(full_text)
        if parsed is not None and isinstance(parsed.get("decision"), str):
            logger.warning(f"[Agent] {seat}号 流式输出为 JSON 包裹，提取发言文本")
            full_text = parsed["decision"]

    return full_text


# ================================================================
# A1: ReAct Agent 入口（决策类专用）
# ================================================================

# 决策类行动类型：走 ReAct 子图
REACT_ACTION_TYPES = {"kill", "verify", "vote", "guard", "poison", "hunter_shoot"}


async def call_agent_react(
    game_state: dict[str, Any],
    player: dict[str, Any],
    action_type: str,
) -> Any:
    """ReAct Agent 决策入口。仅用于决策类行动（kill/verify/vote/guard/poison/hunter_shoot）

    返回决策值（同 call_agent 的返回值类型），并将 ReAct 轨迹持久化。
    """
    from app.agent.react_agent import run_react_agent
    from app.config import get_settings

    settings = get_settings()
    seat = player["seat_number"]
    role = getattr(player["role"], "value", player["role"])

    # DB 路径
    db_path = settings.data_dir / "werewolf.db"

    result = await run_react_agent(
        game_id=game_state.get("game_id", ""),
        db_path=str(db_path),
        seat_number=seat,
        role=role,
        action_type=action_type,
        game_context=game_state,
    )

    # 持久化 AgentLog + agent_steps
    _persist_react_log(
        game_id=game_state.get("game_id", ""),
        round_number=game_state.get("current_round", 0),
        seat_number=seat,
        action_type=action_type,
        result=result,
    )

    # 记录轨迹摘要
    steps = result.get("agent_steps", [])
    tool_calls = [s for s in steps if s["step_type"] == "tool_call"]
    logger.info(
        f"[ReAct] {seat}号({role}) {action_type}: "
        f"decision={result.get('decision')} fallback={result.get('is_fallback')} "
        f"latency={result.get('latency_ms')}ms tools={len(tool_calls)}"
    )

    return result["decision"]


def _persist_react_log(
    game_id: str,
    round_number: int,
    seat_number: int,
    action_type: str,
    result: dict,
) -> None:
    """持久化 ReAct 决策日志 + agent_steps"""
    import threading

    from app.graphs.agent_graph import _get_sync_engine

    def _write():
        try:
            from sqlalchemy.orm import Session

            from app.models.game import AgentLog, AgentStep

            engine = _get_sync_engine()
            with Session(engine) as session:
                log = AgentLog(
                    game_id=game_id,
                    round_number=round_number,
                    seat_number=seat_number,
                    action_type=action_type,
                    parsed_decision=json.dumps(result.get("decision"), ensure_ascii=False, default=str),
                    is_fallback=result.get("is_fallback", False),
                    latency_ms=result.get("latency_ms"),
                    model_name=None,  # ReAct 子图内部路由，不在此记录
                )
                session.add(log)
                session.flush()  # 获取 log.id

                # 写入 agent_steps
                for step in result.get("agent_steps", []):
                    session.add(AgentStep(
                        agent_log_id=log.id,
                        game_id=game_id,
                        step_index=step["step_index"],
                        step_type=step["step_type"],
                        content=step.get("content", "")[:2000],  # 截断过长内容
                    ))
                session.commit()
        except Exception as e:
            logger.warning(f"[ReAct] AgentLog 持久化失败（不影响主链）: {e}")

    threading.Thread(target=_write, daemon=True).start()


# ================================================================
# 通用入口（所有角色的底层实现）
# ================================================================

def call_agent(
    game_state: dict[str, Any],
    player: dict[str, Any],
    action_type: str,
    llm: Any = None,
) -> Any:
    """调用 Agent 推理子图，返回决策值（通用入口）

    参数:
        game_state: 完整的 GameFlowState
        player: 玩家字典（含 seat_number, role, player_type 等）
        action_type: 决策类型（kill/verify/save/poison/speech/vote/last_words）
        llm: 可选的 LLM 实例（默认从配置创建）

    返回:
        决策值（类型取决于 action_type）：
        - kill/verify: int（座位号）
        - save: bool
        - poison/vote: int 或 None
        - speech/last_words: str
    """
    agent_state = {
        "seat_number": player["seat_number"],
        "role": getattr(player["role"], "value", player["role"]),
        "player_name": player.get("player_name", f"{player['seat_number']}号"),
        "action_type": action_type,
        "game_context": game_state,
    }
    if llm is not None:
        agent_state["llm"] = llm

    result = run_agent(agent_state)
    return result["decision"]


# ================================================================
# 狼人专用封装
# ================================================================

def call_agent_werewolf_kill(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> int:
    """狼人选择击杀目标"""
    target = call_agent(game_state, player, "kill", llm)
    if not isinstance(target, int):
        alive_non_wolf = [
            p["seat_number"] for p in game_state.get("players", [])
            if p["is_alive"]
            and p["role"] != "werewolf"
            and p["seat_number"] != player["seat_number"]
        ]
        import random
        target = random.choice(alive_non_wolf) if alive_non_wolf else 1
        logger.warning(f"[Agent] 狼人{player['seat_number']}号 决策异常，降级随机: {target}")
    return target


# ================================================================
# 预言家专用封装
# ================================================================

def call_agent_seer_verify(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> int:
    """预言家选择查验目标"""
    target = call_agent(game_state, player, "verify", llm)
    if not isinstance(target, int):
        alive_others = [
            p["seat_number"] for p in game_state.get("players", [])
            if p["is_alive"] and p["seat_number"] != player["seat_number"]
        ]
        import random
        target = random.choice(alive_others) if alive_others else 1
        logger.warning(f"[Agent] 预言家{player['seat_number']}号 决策异常，降级随机: {target}")
    return target


# ================================================================
# 女巫专用封装
# ================================================================

def call_agent_witch(
    game_state: dict[str, Any],
    player: dict[str, Any],
    save_available: bool,
    poison_available: bool,
    alive_other_seats: list[int],
    llm: Any = None,
) -> tuple[str, Optional[int]]:
    """女巫决策：save/poison/skip 三选一"""
    if save_available:
        save_decision = call_agent(game_state, player, "save", llm)
        if save_decision is True:
            return ("save", None)

    if poison_available:
        poison_decision = call_agent(game_state, player, "poison", llm)
        if poison_decision is not None and poison_decision in alive_other_seats:
            return ("poison", poison_decision)

    return ("skip", None)


def call_agent_guard(
    game_state: dict[str, Any],
    player: dict[str, Any],
    allowed_target_seats: list[int],
    llm: Any = None,
) -> Optional[int]:
    """守卫选择守护目标"""
    scoped_state = {**game_state, "allowed_target_seats": list(allowed_target_seats)}
    target = call_agent(scoped_state, player, "guard", llm)
    if target not in allowed_target_seats:
        target = allowed_target_seats[0] if allowed_target_seats else None
        logger.warning(f"[Agent] 守卫{player['seat_number']}号 决策异常，降级合法目标: {target}")
    return target


def call_agent_hunter_shoot(
    game_state: dict[str, Any],
    player: dict[str, Any],
    allowed_target_seats: list[int],
    trigger: str | None,
    llm: Any = None,
) -> Optional[int]:
    """猎人选择是否开枪"""
    scoped_state = {
        **game_state,
        "allowed_target_seats": list(allowed_target_seats),
        "can_skip": True,
        "pending_hunter_shot": {"seat_number": player["seat_number"], "trigger": trigger},
    }
    target = call_agent(scoped_state, player, "hunter_shoot", llm)
    if target not in allowed_target_seats:
        target = None
        logger.warning(f"[Agent] 猎人{player['seat_number']}号 决策异常，降级为不开枪")
    return target


# ================================================================
# 村民（及所有角色通用）专用封装
# ================================================================

def call_agent_villager_speech(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> str:
    """村民（或任何角色）公开发言"""
    content = call_agent(game_state, player, "speech", llm)
    if not isinstance(content, str) or len(content.strip()) == 0:
        content = f"{player['seat_number']}号选择沉默。"
        logger.warning(f"[Agent] {player['seat_number']}号 发言异常，降级为沉默")
    return content


def call_agent_villager_vote(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> Optional[int]:
    """村民（或任何角色）投票"""
    target = call_agent(game_state, player, "vote", llm)
    if target is not None and not isinstance(target, int):
        target = None
        logger.warning(f"[Agent] {player['seat_number']}号 投票异常，降级为弃票")
    return target


def call_agent_last_words(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> str:
    """任何角色被淘汰时的遗言"""
    content = call_agent(game_state, player, "last_words", llm)
    if not isinstance(content, str) or len(content.strip()) == 0:
        content = f"{player['seat_number']}号没有留下遗言。"
        logger.warning(f"[Agent] {player['seat_number']}号 遗言异常，降级默认文本")
    return content
